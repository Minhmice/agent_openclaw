import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough, Writable } from "node:stream";
import test from "node:test";

import {
  createOpenClawWebPlugin,
  runComponentCallback,
} from "../../deploy/openclaw-web-plugin/index.js";

const MINH_ID = "620891893659598850";
const WIEN_ID = "859783610625556480";
const GUILD_ID = "1446612692910739637";
const CHANNEL_ID = "1536658476288450630";
const MESSAGE_ID = "1537000000000000000";

function makeChild({ stdout = "", stderr = "", exitCode = 0 } = {}) {
  const child = new EventEmitter();
  let input = "";
  child.stdin = new Writable({
    write(chunk, _encoding, callback) {
      input += chunk.toString("utf8");
      callback();
    },
  });
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  child.killed = false;
  child.kill = () => {
    child.killed = true;
    return true;
  };
  child.start = () => {
    queueMicrotask(() => {
      child.stdout.end(stdout);
      child.stderr.end(stderr);
      child.emit("close", exitCode, null);
    });
  };
  child.input = () => input;
  return child;
}

function makeApi({ sendResult } = {}) {
  let registration;
  let cliRegistration;
  const agentCalls = [];
  const outboundCalls = [];
  const warnings = [];
  const config = { marker: "runtime-config" };
  const api = {
    logger: {
      error() {},
      warn(message) {
        warnings.push(message);
      },
    },
    runtime: {
      agent(...args) {
        agentCalls.push(args);
      },
      model(...args) {
        agentCalls.push(args);
      },
      config: {
        current() {
          return config;
        },
      },
      channel: {
        outbound: {
          async loadAdapter(channel) {
            outboundCalls.push({ loadAdapter: channel });
            return {
              async sendPayload(payload) {
                outboundCalls.push({ sendPayload: payload });
                return (
                  sendResult ?? {
                    channel: "discord",
                    messageId: MESSAGE_ID,
                    channelId: CHANNEL_ID,
                  }
                );
              },
            };
          },
        },
      },
    },
    registerCli(registrar, options) {
      cliRegistration = { registrar, options };
    },
    registerInteractiveHandler(value) {
      registration = value;
    },
  };
  return {
    api,
    agentCalls,
    cliRegistration: () => cliRegistration,
    config,
    outboundCalls,
    warnings,
    registration: () => registration,
  };
}

function trustedContext(action, replies) {
  return {
    senderId: MINH_ID,
    guildId: GUILD_ID,
    conversationId: GUILD_ID,
    auth: { isAuthorizedSender: true },
    interaction: {
      data: `openclaw-web:${action}`,
      namespace: "openclaw-web",
      payload: action,
      kind: "button",
      messageId: MESSAGE_ID,
    },
    respond: {
      async followUp(payload) {
        replies.push(payload);
      },
      async reply(payload) {
        replies.push(payload);
      },
    },
  };
}

function callbackContext(payload, replies) {
  const context = trustedContext("approve", replies);
  context.interaction.data = `openclaw-web:${payload}`;
  context.interaction.payload = payload;
  return context;
}

function createTestPlugin(options = {}) {
  return createOpenClawWebPlugin({ guildId: GUILD_ID, ...options });
}

test("registers one deterministic Discord interactive namespace", () => {
  const observed = makeApi();
  createTestPlugin({
    runCallback: async () => ({ message_vi: "Đã ghi nhận thao tác." }),
  }).register(observed.api);

  const registration = observed.registration();
  assert.equal(registration.channel, "discord");
  assert.equal(registration.namespace, "openclaw-web");
  assert.equal(typeof registration.handler, "function");
});

test("uses the approved guild when the gateway omits the non-secret env value", async () => {
  const previousGuildId = process.env.OPENCLAW_WEB_DISCORD_GUILD_ID;
  delete process.env.OPENCLAW_WEB_DISCORD_GUILD_ID;
  try {
    const observed = makeApi();
    const callbacks = [];
    const replies = [];
    createOpenClawWebPlugin({
      async runCallback(envelope) {
        callbacks.push(envelope);
        return { message_vi: "Đã ghi nhận thao tác." };
      },
    }).register(observed.api);

    const result = await observed.registration().handler(
      callbackContext("project:project-1:approve", replies),
    );

    assert.deepEqual(callbacks, [
      {
        actor_id: MINH_ID,
        guild_id: GUILD_ID,
        message_id: MESSAGE_ID,
        value: "project:project-1:approve",
      },
    ]);
    assert.deepEqual(replies, [{ text: "Đã ghi nhận thao tác.", ephemeral: true }]);
    assert.deepEqual(result, { handled: true });
  } finally {
    if (previousGuildId === undefined) {
      delete process.env.OPENCLAW_WEB_DISCORD_GUILD_ID;
    } else {
      process.env.OPENCLAW_WEB_DISCORD_GUILD_ID = previousGuildId;
    }
  }
});

test("registers the fixed openclaw-web send CLI root", () => {
  const observed = makeApi();

  createTestPlugin().register(observed.api);

  assert.deepEqual(observed.cliRegistration().options, {
    descriptors: [
      {
        name: "openclaw-web",
        description: "Bounded OpenClaw Web Discord bridge",
        hasSubcommands: true,
      },
    ],
  });
  assert.equal(typeof observed.cliRegistration().registrar, "function");
});

test("uses trusted Discord identities and handles the callback without an agent", async () => {
  const observed = makeApi();
  const callbacks = [];
  const replies = [];
  createTestPlugin({
    async runCallback(envelope) {
      callbacks.push(envelope);
      return { message_vi: "Đã ghi nhận thao tác." };
    },
  }).register(observed.api);

  const result = await observed.registration().handler(
    callbackContext("project:project-1:approve", replies),
  );

  assert.deepEqual(callbacks, [
    {
      actor_id: MINH_ID,
      guild_id: GUILD_ID,
      message_id: MESSAGE_ID,
      value: "project:project-1:approve",
    },
  ]);
  assert.deepEqual(replies, [{ text: "Đã ghi nhận thao tác.", ephemeral: true }]);
  assert.deepEqual(result, { handled: true });
  assert.deepEqual(observed.agentCalls, []);
});

test("relies on host auto-ack and follows up ephemerally", async () => {
  const observed = makeApi();
  const order = [];
  createTestPlugin({
    async runCallback() {
      order.push("bridge");
      return { message_vi: "Đã ghi nhận thao tác." };
    },
  }).register(observed.api);
  const context = callbackContext("project:project-1:approve", []);
  context.respond.acknowledge = async () => {
    throw new Error("host already acknowledged before the plugin handler");
  };
  context.respond.followUp = async (payload) => {
    order.push(payload);
  };

  const result = await observed.registration().handler(context);

  assert.deepEqual(order, [
    "bridge",
    { text: "Đã ghi nhận thao tác.", ephemeral: true },
  ]);
  assert.deepEqual(result, { handled: true });
  assert.deepEqual(observed.agentCalls, []);
});

test("accepts a host-authorized component when command authorization is false", async () => {
  const observed = makeApi();
  const callbacks = [];
  const replies = [];
  createTestPlugin({
    async runCallback(envelope) {
      callbacks.push(envelope);
      return { message_vi: "Đã ghi nhận thao tác." };
    },
  }).register(observed.api);

  const context = callbackContext("project:project-1:approve", replies);
  // OpenClaw performs the component allowedUsers check before invoking this
  // plugin. Its command authorization result is a separate signal and may be
  // false for an otherwise authorized component actor.
  context.auth.isAuthorizedSender = false;

  const result = await observed.registration().handler(context);

  assert.deepEqual(callbacks, [
    {
      actor_id: MINH_ID,
      guild_id: GUILD_ID,
      message_id: MESSAGE_ID,
      value: "project:project-1:approve",
    },
  ]);
  assert.deepEqual(replies, [{ text: "Đã ghi nhận thao tác.", ephemeral: true }]);
  assert.deepEqual(result, { handled: true });
});

test("logs a redacted envelope rejection reason", async () => {
  const observed = makeApi();
  const replies = [];
  createTestPlugin().register(observed.api);
  const context = callbackContext("project:project-1:approve", replies);
  context.interaction.messageId = "not-a-message";

  await observed.registration().handler(context);

  assert.deepEqual(observed.warnings, [
    "openclaw-web invalid component envelope: invalid_message",
  ]);
  assert.deepEqual(replies, [
    { text: "Yêu cầu từ nút bấm không hợp lệ.", ephemeral: true },
  ]);
});

for (const payload of [
  "",
  "approve",
  "project:project-1:approve:extra",
  "project:project-1:unknown-action",
  "project::approve",
  "/approve",
  "APPROVE",
  "__proto__",
  "unknown-action",
]) {
  test(`rejects malformed callback action ${JSON.stringify(payload)} without execution`, async () => {
    const observed = makeApi();
    let executions = 0;
    const replies = [];
    createTestPlugin({
      async runCallback() {
        executions += 1;
        return { message_vi: "unexpected" };
      },
    }).register(observed.api);
    const context = callbackContext(payload, replies);

    const result = await observed.registration().handler(context);

    assert.equal(executions, 0);
    assert.deepEqual(replies, [
      { text: "Yêu cầu từ nút bấm không hợp lệ.", ephemeral: true },
    ]);
    assert.deepEqual(result, { handled: true });
    assert.deepEqual(observed.agentCalls, []);
  });
}

test("rejects an untrusted or incomplete Discord context without execution", async () => {
  const cases = [
    (ctx) => {
      delete ctx.senderId;
    },
    (ctx) => {
      delete ctx.guildId;
    },
    (ctx) => {
      ctx.guildId = "999999999999999999";
    },
    (ctx) => {
      delete ctx.interaction.messageId;
    },
    (ctx) => {
      ctx.interaction.namespace = "other";
    },
  ];
  for (const alter of cases) {
    const observed = makeApi();
    let executions = 0;
    const replies = [];
    createTestPlugin({
      async runCallback() {
        executions += 1;
        return { message_vi: "unexpected" };
      },
    }).register(observed.api);
    const context = callbackContext("project:project-1:approve", replies);
    alter(context);

    const result = await observed.registration().handler(context);

    assert.equal(executions, 0);
    assert.deepEqual(result, { handled: true });
    assert.equal(replies.at(0)?.ephemeral, true);
    assert.deepEqual(observed.agentCalls, []);
  }
});

test("keeps subprocess payload off argv and bounds the bridge", async () => {
  const child = makeChild({
    stdout: JSON.stringify({
      status: "accepted",
      message_vi: "Đã ghi nhận thao tác.",
      fallback_command: "/lead-approve project-1",
    }),
  });
  const calls = [];
  const envelope = {
    actor_id: MINH_ID,
    guild_id: GUILD_ID,
    message_id: MESSAGE_ID,
    value: "project:project-1:approve",
  };
  const pending = runComponentCallback(envelope, {
    commandPath: "/home/minhmice/.local/share/openclaw-web/current/venv/bin/openclaw-web",
    timeoutMs: 1000,
    spawnProcess(file, args, options) {
      calls.push({ file, args, options });
      child.start();
      return child;
    },
  });

  const result = await pending;

  assert.deepEqual(calls, [
    {
      file: "/home/minhmice/.local/share/openclaw-web/current/venv/bin/openclaw-web",
      args: ["component-callback", "--json"],
      options: {
        detached: process.platform !== "win32",
        shell: false,
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
      },
    },
  ]);
  assert.equal(child.input(), `${JSON.stringify(envelope)}\n`);
  assert.deepEqual(result, {
    status: "accepted",
    message_vi: "Đã ghi nhận thao tác.",
    fallback_command: "/lead-approve project-1",
  });
});

test("kills a callback whose stdout exceeds the fixed bound", async () => {
  const child = makeChild({ stdout: "x".repeat(65_537) });
  const pending = runComponentCallback(
    {
      actor_id: MINH_ID,
      guild_id: GUILD_ID,
      message_id: MESSAGE_ID,
      value: "project:project-1:approve",
    },
    {
      commandPath: "/opt/openclaw-web",
      timeoutMs: 1000,
      spawnProcess() {
        child.start();
        return child;
      },
    },
  );

  await assert.rejects(pending, /output too large/);
  assert.equal(child.killed, true);
});

test("kills a callback after the finite timeout", async () => {
  const child = makeChild();
  const pending = runComponentCallback(
    {
      actor_id: MINH_ID,
      guild_id: GUILD_ID,
      message_id: MESSAGE_ID,
      value: "project:project-1:approve",
    },
    {
      commandPath: "/opt/openclaw-web",
      timeoutMs: 5,
      spawnProcess() {
        return child;
      },
    },
  );

  await assert.rejects(pending, /timed out/);
  assert.equal(child.killed, true);
});

test("converts bridge failures into an ephemeral Vietnamese response and stays handled", async () => {
  const observed = makeApi();
  const replies = [];
  createTestPlugin({
    async runCallback() {
      throw new Error("private child stderr must not be returned");
    },
  }).register(observed.api);

  const result = await observed.registration().handler(
    callbackContext("project:project-1:approve", replies),
  );

  assert.deepEqual(replies, [
    {
      text: "Chưa thể xử lý thao tác lúc này. Vui lòng thử lại.",
      ephemeral: true,
    },
  ]);
  assert.deepEqual(result, { handled: true });
  assert.deepEqual(observed.agentCalls, []);
});

test("sends native Discord components through the runtime adapter without an agent", async () => {
  const observed = makeApi();
  const nativeSpec = {
    reusable: true,
    blocks: [
      {
        type: "actions",
        buttons: [
          {
            label: "Approve",
            style: "success",
            callbackData: "openclaw-web:project:project-1:approve",
            callbackDataKind: "callback",
            allowedUsers: [MINH_ID, WIEN_ID],
          },
        ],
      },
    ],
  };

  let output = "";
  createTestPlugin({
    readOutboundInput: async () =>
      `${JSON.stringify({
        channel_id: CHANNEL_ID,
        guild_id: GUILD_ID,
        idempotency_key: "review:project-1",
        text: "Duyá»‡t lead project-1",
        components: nativeSpec,
      })}\n`,
    writeOutboundOutput(text) {
      output += text;
    },
  }).register(observed.api);
  const program = makeProgram();
  observed.cliRegistration().registrar({ program });

  await program.invoke(["openclaw-web", "send"], { json: true });

  assert.deepEqual(observed.outboundCalls, [
    { loadAdapter: "discord" },
    {
      sendPayload: {
        cfg: observed.config,
        to: `channel:${CHANNEL_ID}`,
        text: "Duyá»‡t lead project-1",
        payload: {
          text: "Duyá»‡t lead project-1",
          channelData: { discord: { components: nativeSpec } },
        },
      },
    },
  ]);
  assert.deepEqual(JSON.parse(output), {
    channel_id: CHANNEL_ID,
    message_id: MESSAGE_ID,
  });
  assert.deepEqual(observed.agentCalls, []);
});

function makeProgram() {
  const roots = new Map();
  function commandNode(name) {
    const children = new Map();
    const node = {
      _action: undefined,
      command(spec) {
        const child = commandNode(spec.split(/[ <]/, 1)[0]);
        children.set(spec.split(/[ <]/, 1)[0], child);
        return child;
      },
      description() {
        return node;
      },
      option() {
        return node;
      },
      action(handler) {
        node._action = handler;
        return node;
      },
      children,
    };
    return node;
  }
  return {
    command(name) {
      const root = commandNode(name);
      roots.set(name, root);
      return root;
    },
    async invoke(path, options) {
      const root = roots.get(path[0]);
      const child = root?.children.get(path[1]);
      assert.equal(typeof child?._action, "function");
      await child._action(options);
    },
  };
}
