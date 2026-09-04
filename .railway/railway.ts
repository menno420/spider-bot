import { defineRailway, github, preserve, project, service } from "railway/iac";

export default defineRailway(() => {
  const worker = service("worker", {
    source: github("menno420/spider-bot", { checkSuites: false }),
    build: {
      builder: "RAILPACK",
      watchPatterns: ["spiderbot/**", "requirements.txt", ".python-version"],
    },
    replicas: { "europe-west4-drams3a": 1 },
    deploy: {
      limitOverride: { containers: { cpu: 1, memoryBytes: 1000000000 } },
    },
    env: {
      ANTHROPIC_API_KEY: preserve(),
      DISCORD_TOKEN: preserve(),
      GUILD_ID: preserve(),
      // The rollout switches (docs/rollout.md). Each is set in the Railway
      // dashboard when its step comes, never here: preserve() means "keep
      // whatever Railway holds", and in IaC omit means delete — so a switch
      // set in the dashboard but absent from this file would be removed by
      // the next `railway config apply`. Measured 2026-09-04 with a read-only
      // plan: preserve() on a variable that does not exist yet is a no-op.
      GITHUB_TOKEN: preserve(),
      INTAKE_PUBLISH_ENABLED: preserve(),
      MOD_MODE: preserve(),
      MOD_WATCH_CHANNELS: preserve(),
      MOD_CEILING: preserve(),
      SUPPORT_FEED_URL: preserve(),
    },
    start: "python -m spiderbot",
  });

  return project("spider-bot", {
    resources: [worker],
  });
});
