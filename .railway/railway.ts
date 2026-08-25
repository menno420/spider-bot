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
    },
    start: "python -m spiderbot",
  });

  return project("spider-bot", {
    resources: [worker],
  });
});
