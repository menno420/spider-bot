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
      // Every variable `.env.example` documents, with preserve(): the value
      // lives in the Railway dashboard, never here, and in IaC omit means
      // delete — a variable set there but absent from this file is removed
      // by the next `railway config apply`. preserve() on a variable that
      // does not exist yet is a no-op (measured 2026-09-04 with a read-only
      // plan), so the list is inert until a value is set. Kept in step by
      // tests/test_config.py — the test reads both files.
      DISCORD_TOKEN: preserve(),
      GUILD_ID: preserve(),
      LOG_LEVEL: preserve(),
      ANTHROPIC_API_KEY: preserve(),
      AI_ENABLED: preserve(),
      AI_MODEL: preserve(),
      AI_EFFORT: preserve(),
      AI_MAX_RESPONSE_TOKENS: preserve(),
      AI_MEMORY_TURNS: preserve(),
      AI_INITIATIVE_CHANNELS: preserve(),
      AI_INITIATIVE_COOLDOWN_SECONDS: preserve(),
      AI_INITIATIVE_HOURLY_CAP: preserve(),
      GITHUB_TOKEN: preserve(),
      GITHUB_REPO: preserve(),
      GITHUB_REPO_BOT: preserve(),
      INTAKE_PUBLISH_ENABLED: preserve(),
      MOD_MODE: preserve(),
      MOD_WATCH_CHANNELS: preserve(),
      MOD_CEILING: preserve(),
      MOD_MODEL: preserve(),
      SUPPORT_FEED_URL: preserve(),
      SUPPORT_FEED_REFRESH_SECONDS: preserve(),
    },
    start: "python -m spiderbot",
  });

  return project("spider-bot", {
    resources: [worker],
  });
});
