import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";
const edgePath = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
export default defineConfig({
  testDir: "./tests",
  // Earlier workspace UI specs remain as historical sources, superseded by this entry design.
  testMatch: [
    "**/project-home.spec.ts",
    "**/project-data.spec.ts",
    "**/project-overview.spec.ts",
  ],
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:5176",
    viewport: { width: 1440, height: 900 },
    launchOptions: {
      executablePath:
        process.env.DEMO_BROWSER ||
        (existsSync(edgePath) ? edgePath : undefined),
    },
  },
  webServer: {
    command: "npm run dev",
    url: "http://127.0.0.1:5176",
    reuseExistingServer: !process.env.CI,
  },
});
