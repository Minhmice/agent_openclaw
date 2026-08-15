import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { TextDecoder } from "node:util";

const require = createRequire(import.meta.url);
let definePluginEntry;
try {
  ({ definePluginEntry } = require("openclaw/plugin-sdk/plugin-entry"));
} catch {
  // The peer package is supplied by OpenClaw on the host. Keeping a local
  // identity fallback lets the pure bridge tests run without installing the
  // host application; the remote installer still validates the real helper.
  definePluginEntry = (entry) => entry;
}

const PLUGIN_ID = "openclaw-web-components";
const NAMESPACE = "openclaw-web";
// This is a non-secret deployment identifier. Keep a safe default so inbound
// gateway callbacks remain verifiable even when the gateway does not inherit
// the discovery service's environment file.
const DEFAULT_GUILD_ID = "1446612692910739637";
const DEFAULT_COMMAND =
  `${process.env.HOME ?? ""}/.local/share/openclaw-web/current/venv/bin/openclaw-web`;
const DEFAULT_TIMEOUT_MS = 60_000;
const MAX_BYTES = 65_536;
const MAX_TIMEOUT_MS = 90_000;
const DISCORD_SNOWFLAKE = /^\d{17,20}$/;
const IDEMPOTENCY_KEY = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/;
const OUTBOUND_KEYS = new Set([
  "channel_id",
  "components",
  "guild_id",
  "idempotency_key",
  "text",
]);
const BUTTON_KEYS = new Set([
  "allowedUsers",
  "callbackData",
  "callbackDataKind",
  "label",
  "style",
]);
const BUTTON_STYLES = new Set(["danger", "primary", "secondary", "success"]);
const PROJECT_ID = "[A-Za-z0-9][A-Za-z0-9._-]{0,127}";
const PAGE_SLUG = "[A-Za-z0-9][A-Za-z0-9._-]{0,127}";
const PROJECT_CALLBACK = new RegExp(
  `^project:${PROJECT_ID}:(${[
    "approve",
    "reject",
    "request-changes",
    "final-confirm",
    "view-evidence",
    "view-unresolved",
    "refresh",
  ].join("|")})$`,
);
const PAGE_CALLBACK = new RegExp(
  `^project:${PROJECT_ID}:page:${PAGE_SLUG}:(${[
    "page-status",
    "mark-done",
    "block",
    "page-approve",
    "refresh",
  ].join("|")})$`,
);

async function respondEphemeral(ctx, text) {
  const payload = { text, ephemeral: true };
  if (typeof ctx?.respond?.followUp === "function") {
    await ctx.respond.followUp(payload).catch(() => undefined);
    return;
  }
  if (typeof ctx?.respond?.reply === "function") {
    await ctx.respond.reply(payload).catch(() => undefined);
  }
}

function trustedEnvelopeCheck(ctx, configuredGuildId) {
  const actorId = ctx?.senderId;
  const guildId = ctx?.guildId ?? ctx?.rawGuildId;
  const messageId = ctx?.interaction?.messageId;
  const namespace = ctx?.interaction?.namespace;
  const value = ctx?.interaction?.payload;
  // OpenClaw has already enforced the component's allowedUsers and guild /
  // channel policy before invoking this handler. Its auth flag represents
  // command authorization, which is a separate signal and may be false for a
  // valid component actor. The durable Python callback validates the actor,
  // message, component state, and action again before any mutation.
  if (typeof actorId !== "string" || !DISCORD_SNOWFLAKE.test(actorId)) {
    return { envelope: null, reason: "invalid_actor" };
  }
  if (
    typeof configuredGuildId !== "string" ||
    !DISCORD_SNOWFLAKE.test(configuredGuildId)
  ) {
    return { envelope: null, reason: "invalid_configured_guild" };
  }
  if (guildId !== configuredGuildId) {
    return { envelope: null, reason: "guild_mismatch" };
  }
  if (typeof messageId !== "string" || !DISCORD_SNOWFLAKE.test(messageId)) {
    return { envelope: null, reason: "invalid_message" };
  }
  if (namespace !== NAMESPACE) {
    return { envelope: null, reason: "namespace_mismatch" };
  }
  if (
    typeof value !== "string" ||
    value.length > 512 ||
    (!PROJECT_CALLBACK.test(value) && !PAGE_CALLBACK.test(value))
  ) {
    return { envelope: null, reason: "invalid_payload" };
  }
  return {
    envelope: {
      actor_id: actorId,
      guild_id: guildId,
      message_id: messageId,
      value,
    },
    reason: null,
  };
}

function isPlainObject(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype
  );
}

function hasExactKeys(value, allowed, required = allowed) {
  const keys = Object.keys(value);
  return (
    keys.every((key) => allowed.has(key)) &&
    [...required].every((key) => keys.includes(key))
  );
}

function validateNativeComponents(value) {
  if (
    !isPlainObject(value) ||
    !hasExactKeys(value, new Set(["blocks", "reusable"])) ||
    value.reusable !== true ||
    !Array.isArray(value.blocks) ||
    value.blocks.length < 1 ||
    value.blocks.length > 5
  ) {
    throw new Error("invalid outbound request");
  }
  for (const block of value.blocks) {
    if (
      !isPlainObject(block) ||
      !hasExactKeys(block, new Set(["buttons", "type"])) ||
      block.type !== "actions" ||
      !Array.isArray(block.buttons) ||
      block.buttons.length < 1 ||
      block.buttons.length > 5
    ) {
      throw new Error("invalid outbound request");
    }
    for (const button of block.buttons) {
      const callback = button?.callbackData;
      const valueText =
        typeof callback === "string" && callback.startsWith(`${NAMESPACE}:`)
          ? callback.slice(NAMESPACE.length + 1)
          : "";
      if (
        !isPlainObject(button) ||
        !hasExactKeys(button, BUTTON_KEYS) ||
        typeof button.label !== "string" ||
        !button.label.trim() ||
        Buffer.byteLength(button.label) > 80 ||
        !BUTTON_STYLES.has(button.style) ||
        button.callbackDataKind !== "callback" ||
        typeof callback !== "string" ||
        Buffer.byteLength(callback) > 512 ||
        (!PROJECT_CALLBACK.test(valueText) && !PAGE_CALLBACK.test(valueText)) ||
        !Array.isArray(button.allowedUsers) ||
        button.allowedUsers.length < 1 ||
        button.allowedUsers.length > 10 ||
        button.allowedUsers.some(
          (actorId) => typeof actorId !== "string" || !DISCORD_SNOWFLAKE.test(actorId),
        ) ||
        new Set(button.allowedUsers).size !== button.allowedUsers.length
      ) {
        throw new Error("invalid outbound request");
      }
    }
  }
}

function parseOutboundRequest(text, configuredGuildId) {
  if (typeof text !== "string" || Buffer.byteLength(text) > MAX_BYTES) {
    throw new Error("invalid outbound request");
  }
  let payload;
  try {
    payload = JSON.parse(text);
  } catch {
    throw new Error("invalid outbound request");
  }
  if (
    !isPlainObject(payload) ||
    !hasExactKeys(payload, OUTBOUND_KEYS) ||
    typeof payload.channel_id !== "string" ||
    !DISCORD_SNOWFLAKE.test(payload.channel_id) ||
    typeof payload.guild_id !== "string" ||
    !DISCORD_SNOWFLAKE.test(payload.guild_id) ||
    payload.guild_id !== configuredGuildId ||
    typeof payload.idempotency_key !== "string" ||
    !IDEMPOTENCY_KEY.test(payload.idempotency_key) ||
    typeof payload.text !== "string" ||
    !payload.text.trim() ||
    payload.text !== payload.text.trim() ||
    Buffer.byteLength(payload.text) > 2_000
  ) {
    throw new Error("invalid outbound request");
  }
  validateNativeComponents(payload.components);
  return payload;
}

async function readBoundedUtf8(stream = process.stdin) {
  let bytes = 0;
  const chunks = [];
  for await (const chunk of stream) {
    const value = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    bytes += value.length;
    if (bytes > MAX_BYTES) {
      throw new Error("invalid outbound request");
    }
    chunks.push(value);
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks));
  } catch {
    throw new Error("invalid outbound request");
  }
}

function normalizeSendResult(result, expectedChannelId) {
  const values = Array.isArray(result) ? result : [result];
  const identity = values[0];
  if (values.length !== 1 || !isPlainObject(identity)) {
    throw new Error("Discord adapter returned an invalid identity");
  }
  const messageId = identity.messageId ?? identity.message_id;
  const channelId = identity.channelId ?? identity.channel_id ?? identity.chatId;
  if (
    typeof messageId !== "string" ||
    !DISCORD_SNOWFLAKE.test(messageId) ||
    channelId !== expectedChannelId
  ) {
    throw new Error("Discord adapter returned an invalid identity");
  }
  return { channel_id: channelId, message_id: messageId };
}

async function sendOutbound(api, request) {
  const adapter = await api.runtime.channel.outbound.loadAdapter("discord");
  if (typeof adapter?.sendPayload !== "function") {
    throw new Error("Discord adapter is unavailable");
  }
  const result = await adapter.sendPayload({
    cfg: api.runtime.config.current(),
    to: `channel:${request.channel_id}`,
    text: request.text,
    payload: {
      text: request.text,
      channelData: { discord: { components: request.components } },
    },
  });
  return normalizeSendResult(result, request.channel_id);
}

function collectBounded(stream, onLimit) {
  let bytes = 0;
  const chunks = [];
  stream.on("data", (chunk) => {
    const value = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    bytes += value.length;
    if (bytes > MAX_BYTES) {
      onLimit();
      return;
    }
    chunks.push(value);
  });
  return () => Buffer.concat(chunks).toString("utf8");
}

export function runComponentCallback(
  envelope,
  {
    commandPath = DEFAULT_COMMAND,
    timeoutMs = DEFAULT_TIMEOUT_MS,
    spawnProcess = spawn,
    platform = process.platform,
    killProcess = process.kill,
  } = {},
) {
  if (
    typeof commandPath !== "string" ||
    !commandPath.startsWith("/") ||
    !Number.isSafeInteger(timeoutMs) ||
    timeoutMs < 1 ||
    timeoutMs > MAX_TIMEOUT_MS
  ) {
    return Promise.reject(new Error("invalid bridge configuration"));
  }
  const input = `${JSON.stringify(envelope)}\n`;
  if (Buffer.byteLength(input) > MAX_BYTES) {
    return Promise.reject(new Error("callback envelope too large"));
  }
  return new Promise((resolve, reject) => {
    let settled = false;
    const child = spawnProcess(
      commandPath,
      ["component-callback", "--json"],
      {
        detached: platform !== "win32",
        shell: false,
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
      },
    );
    const killChild = () => {
      if (platform !== "win32" && Number.isSafeInteger(child.pid) && child.pid > 0) {
        try {
          killProcess(-child.pid, "SIGKILL");
          return;
        } catch {
          // The process group may have exited before the overflow/timeout handler.
        }
      }
      child.kill("SIGKILL");
    };
    const fail = (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error);
    };
    const timer = setTimeout(() => {
      killChild();
      fail(new Error("component callback timed out"));
    }, timeoutMs);
    timer.unref?.();
    const stdout = collectBounded(child.stdout, () => {
      killChild();
      fail(new Error("component callback output too large"));
    });
    collectBounded(child.stderr, () => {
      killChild();
      fail(new Error("component callback error output too large"));
    });
    child.once("error", () => fail(new Error("component callback could not start")));
    child.once("close", (code, signal) => {
      if (settled) return;
      clearTimeout(timer);
      if (code !== 0 || signal !== null) {
        fail(new Error("component callback failed"));
        return;
      }
      let decoded;
      try {
        decoded = JSON.parse(stdout());
      } catch {
        fail(new Error("component callback returned invalid JSON"));
        return;
      }
      if (
        decoded === null ||
        typeof decoded !== "object" ||
        Array.isArray(decoded) ||
        typeof decoded.status !== "string" ||
        typeof decoded.message_vi !== "string" ||
        !decoded.message_vi.trim()
      ) {
        fail(new Error("component callback returned an invalid result"));
        return;
      }
      settled = true;
      resolve(decoded);
    });
    child.stdin.once("error", () => fail(new Error("component callback input failed")));
    child.stdin.end(input);
  });
}

export function createOpenClawWebPlugin({
  guildId = process.env.OPENCLAW_WEB_DISCORD_GUILD_ID ?? DEFAULT_GUILD_ID,
  readOutboundInput = readBoundedUtf8,
  runCallback = runComponentCallback,
  writeOutboundOutput = (text) => process.stdout.write(text),
} = {}) {
  return definePluginEntry({
    id: PLUGIN_ID,
    name: "OpenClaw Web Components",
    description: "Deterministic Discord Components bridge for website review.",
    register(api) {
      api.registerCli(
        ({ program }) => {
          const root = program
            .command("openclaw-web")
            .description("Bounded OpenClaw Web Discord bridge");
          root
            .command("send")
            .description("Send one native Discord component payload from bounded stdin")
            .option("--json", "Read and emit strict JSON")
            .action(async (options) => {
              if (options?.json !== true) {
                throw new Error("--json is required");
              }
              const request = parseOutboundRequest(await readOutboundInput(), guildId);
              const result = await sendOutbound(api, request);
              writeOutboundOutput(`${JSON.stringify(result)}\n`);
            });
        },
        {
          descriptors: [
            {
              name: "openclaw-web",
              description: "Bounded OpenClaw Web Discord bridge",
              hasSubcommands: true,
            },
          ],
        },
      );
      api.registerInteractiveHandler({
        channel: "discord",
        namespace: NAMESPACE,
        handler: async (ctx) => {
          const checked = trustedEnvelopeCheck(ctx, guildId);
          if (checked.envelope === null) {
            api.logger?.warn?.(
              `openclaw-web invalid component envelope: ${checked.reason}`,
            );
            await respondEphemeral(ctx, "Yêu cầu từ nút bấm không hợp lệ.");
            return { handled: true };
          }
          try {
            const result = await runCallback(checked.envelope);
            await respondEphemeral(ctx, result.message_vi);
          } catch {
            api.logger?.error?.("openclaw-web component callback failed");
            const message = "Chưa thể xử lý thao tác lúc này. Vui lòng thử lại.";
            await respondEphemeral(ctx, message);
          }
          return { handled: true };
        },
      });
    },
  });
}

export default createOpenClawWebPlugin();
