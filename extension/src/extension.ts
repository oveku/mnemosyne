import * as http from 'http';
import * as https from 'https';
import { URL } from 'url';
import * as vscode from 'vscode';

// --- Types ---

type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

interface BootstrapItem {
    id: number | string;
    kind: string;
    title: string;
    content: string;
    tags: string;
    updated_at: string;
}

interface BootstrapResult {
    pinned: BootstrapItem[];
    recent: BootstrapItem[];
}

interface SessionDraft {
    summary: string;
    decisions: string[];
    nextSteps: string[];
    updatedAt: string;
}

interface MnemosyneConfig {
    serverUrl: string;
    autoBootstrap: boolean;
    autoCommitOnClose: boolean;
    workspaceHint: string;
}

// --- Constants ---

const DRAFT_KEY = 'mnemosyne.sessionDraft';
const PROMPTED_KEY = 'mnemosyne.summaryPrompted';

// --- Module state ---

let extensionContext: vscode.ExtensionContext | undefined;
let outputChannel: vscode.OutputChannel | undefined;

// --- MCP Client ---

class MnemosyneClient {
    constructor(private readonly serverUrl: string) { }

    async bootstrap(): Promise<BootstrapResult> {
        return this.callTool<BootstrapResult>('mnemosyne_bootstrap', {});
    }

    async commitSession(
        workspaceHint: string,
        summary: string,
        decisions: string[],
        nextSteps: string[]
    ): Promise<JsonValue> {
        return this.callTool<JsonValue>('mnemosyne_commit_session', {
            workspace_hint: workspaceHint,
            summary,
            decisions_json: JSON.stringify(decisions),
            next_steps_json: JSON.stringify(nextSteps),
        });
    }

    private async callTool<T>(name: string, args: Record<string, JsonValue>): Promise<T> {
        const response = await requestJsonRpc(this.serverUrl, 'tools/call', {
            name,
            arguments: args,
        });

        if (!response || typeof response !== 'object') {
            throw new Error('Invalid response from Mnemosyne server.');
        }

        const result = response.result as { content?: Array<{ text?: string }> } | undefined;
        const text = result?.content?.[0]?.text;

        if (!text) {
            throw new Error('Mnemosyne response did not include tool content.');
        }

        return JSON.parse(text) as T;
    }
}

// --- Extension lifecycle ---

export async function activate(context: vscode.ExtensionContext): Promise<void> {
    extensionContext = context;
    outputChannel = vscode.window.createOutputChannel('Mnemosyne');
    context.subscriptions.push(outputChannel);

    // Register commands
    context.subscriptions.push(
        vscode.commands.registerCommand('mnemosyne.setSessionSummary', async () => {
            const config = getConfig();
            await promptAndStoreDraft(context, config, true);
        })
    );

    context.subscriptions.push(
        vscode.commands.registerCommand('mnemosyne.commitSession', async () => {
            const config = getConfig();
            await commitSession(context, config, true);
        })
    );

    context.subscriptions.push(
        vscode.commands.registerCommand('mnemosyne.showBootstrap', async () => {
            const config = getConfig();
            await showBootstrap(config);
        })
    );

    // Prompt for summary when window loses focus
    context.subscriptions.push(
        vscode.window.onDidChangeWindowState(async (state) => {
            if (!state.focused) {
                const config = getConfig();
                await promptAndStoreDraft(context, config, false);
            }
        })
    );

    // Auto-bootstrap on startup
    const config = getConfig();
    if (config.autoBootstrap) {
        await showBootstrap(config);
    }
}

export async function deactivate(): Promise<void> {
    if (!extensionContext) {
        return;
    }

    const config = getConfig();
    if (!config.autoCommitOnClose) {
        return;
    }

    await commitSession(extensionContext, config, false);
}

// --- Configuration ---

function getConfig(): MnemosyneConfig {
    const config = vscode.workspace.getConfiguration('mnemosyne');
    return {
        serverUrl: config.get<string>('serverUrl', ''),
        autoBootstrap: config.get<boolean>('autoBootstrap', true),
        autoCommitOnClose: config.get<boolean>('autoCommitOnClose', true),
        workspaceHint: config.get<string>('workspaceHint', ''),
    };
}

// --- Bootstrap ---

async function showBootstrap(config: MnemosyneConfig): Promise<void> {
    if (!config.serverUrl) {
        log('Mnemosyne serverUrl is not configured.');
        return;
    }

    const client = new MnemosyneClient(config.serverUrl);
    try {
        const result = await client.bootstrap();
        log('=== MNEMOSYNE BOOTSTRAP ===');

        log('Pinned Items:');
        if (!result.pinned.length) {
            log('  (none)');
        } else {
            for (const item of result.pinned) {
                log(`  [${item.kind}] ${item.title}`);
            }
        }

        log('Recent Items:');
        if (!result.recent.length) {
            log('  (none)');
        } else {
            for (const item of result.recent) {
                log(`  [${item.kind}] ${item.title}`);
            }
        }
    } catch (error) {
        log(`Bootstrap failed: ${stringifyError(error)}`);
    }
}

// --- Session commit ---

async function commitSession(
    context: vscode.ExtensionContext,
    config: MnemosyneConfig,
    forcePrompt: boolean
): Promise<void> {
    if (!config.serverUrl) {
        log('Mnemosyne serverUrl is not configured.');
        return;
    }

    if (forcePrompt) {
        await promptAndStoreDraft(context, config, true);
    }

    const draft = context.workspaceState.get<SessionDraft>(DRAFT_KEY);
    const summary = draft?.summary?.trim() || 'Auto-commit: no summary provided.';
    const decisions = draft?.decisions ?? [];
    const nextSteps = draft?.nextSteps ?? [];
    const workspaceHint = getWorkspaceHint(config);

    const client = new MnemosyneClient(config.serverUrl);
    try {
        await client.commitSession(workspaceHint, summary, decisions, nextSteps);
        log('Session committed to Mnemosyne.');
        await context.workspaceState.update(DRAFT_KEY, undefined);
        await context.workspaceState.update(PROMPTED_KEY, undefined);
    } catch (error) {
        log(`Commit failed: ${stringifyError(error)}`);
    }
}

// --- Session draft prompting ---

async function promptAndStoreDraft(
    context: vscode.ExtensionContext,
    config: MnemosyneConfig,
    forcePrompt: boolean
): Promise<void> {
    const alreadyPrompted = context.workspaceState.get<boolean>(PROMPTED_KEY);
    if (alreadyPrompted && !forcePrompt) {
        return;
    }

    const summary = await vscode.window.showInputBox({
        title: 'Mnemosyne Session Summary',
        prompt: 'Describe what changed and why (single paragraph).',
        ignoreFocusOut: true,
    });

    if (summary === undefined && !forcePrompt) {
        await context.workspaceState.update(PROMPTED_KEY, true);
        return;
    }

    const decisionsInput = await vscode.window.showInputBox({
        title: 'Mnemosyne Decisions',
        prompt: 'List decisions separated by semicolons (optional).',
        ignoreFocusOut: true,
    });

    const nextStepsInput = await vscode.window.showInputBox({
        title: 'Mnemosyne Next Steps',
        prompt: 'List next steps separated by semicolons (optional).',
        ignoreFocusOut: true,
    });

    const draft: SessionDraft = {
        summary: (summary ?? '').trim() || 'Auto-commit: no summary provided.',
        decisions: splitList(decisionsInput),
        nextSteps: splitList(nextStepsInput),
        updatedAt: new Date().toISOString(),
    };

    await context.workspaceState.update(DRAFT_KEY, draft);
    await context.workspaceState.update(PROMPTED_KEY, true);
    log('Session summary stored.');
}

// --- Utilities ---

function getWorkspaceHint(config: MnemosyneConfig): string {
    if (config.workspaceHint.trim()) {
        return config.workspaceHint.trim();
    }
    const folder = vscode.workspace.workspaceFolders?.[0];
    return folder?.name ?? 'global';
}

function splitList(value?: string): string[] {
    if (!value) {
        return [];
    }
    return value
        .split(';')
        .map((item) => item.trim())
        .filter((item) => item.length > 0);
}

function log(message: string): void {
    if (!outputChannel) {
        return;
    }
    outputChannel.appendLine(message);
    outputChannel.show(true);
}

function stringifyError(error: unknown): string {
    if (error instanceof Error) {
        return error.message;
    }
    return String(error);
}

// --- HTTP transport ---

function requestJsonRpc(url: string, method: string, params: JsonValue): Promise<any> {
    const urlObj = new URL(url);
    const isHttps = urlObj.protocol === 'https:';
    const payload = JSON.stringify({
        jsonrpc: '2.0',
        id: Date.now(),
        method,
        params,
    });

    const options: http.RequestOptions = {
        method: 'POST',
        hostname: urlObj.hostname,
        port: urlObj.port ? Number(urlObj.port) : undefined,
        path: `${urlObj.pathname}${urlObj.search}`,
        headers: {
            'Content-Type': 'application/json',
            'Content-Length': Buffer.byteLength(payload),
        },
    };

    return new Promise((resolve, reject) => {
        const requestFn = isHttps ? https.request : http.request;
        const req = requestFn(options, (res) => {
            let data = '';
            res.on('data', (chunk: string) => {
                data += chunk;
            });
            res.on('end', () => {
                try {
                    const parsed = JSON.parse(data);
                    if (parsed.error) {
                        reject(new Error(parsed.error.message || 'MCP error.'));
                        return;
                    }
                    resolve(parsed);
                } catch {
                    reject(new Error('Invalid JSON response from Mnemosyne server.'));
                }
            });
        });

        req.on('error', (error: Error) => {
            reject(error);
        });

        req.write(payload);
        req.end();
    });
}
