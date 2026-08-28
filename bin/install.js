#!/usr/bin/env node
// bin/install.js — Installs the agent-board skill to ~/.agents/skills/agent-board (or custom directory)

const fs = require("fs");
const os = require("os");
const path = require("path");

const SKILL_NAME = "agent-board";
const REPO_ROOT = path.join(__dirname, "..");
const SKILL_DIR = path.join(REPO_ROOT, "skill");
const HOME = os.homedir();

const args = process.argv.slice(2);
const has = (f) => args.includes(f);

if (has("-h") || has("--help")) {
  console.log(`
agent-board-skill installer

Usage:
  node bin/install.js                  # Symlink to ~/.agents/skills/${SKILL_NAME} (Default)
  node bin/install.js --claude         # Symlink to ~/.claude/skills/${SKILL_NAME}
  node bin/install.js --codex          # Symlink to ~/.config/opencode/skills/${SKILL_NAME}
  node bin/install.js --dir <path>     # Install to custom skills root directory
  node bin/install.js --force          # Overwrite existing installation or symlink
  node bin/install.js --copy           # Copy files instead of creating symlink
`);
  process.exit(0);
}

let root = path.join(HOME, ".agents", "skills");
const dirIdx = args.indexOf("--dir");
if (dirIdx !== -1) {
  if (!args[dirIdx + 1]) {
    console.error("Error: --dir requires a path argument.");
    process.exit(1);
  }
  root = path.resolve(args[dirIdx + 1]);
} else if (has("--claude")) {
  root = path.join(HOME, ".claude", "skills");
} else if (has("--codex")) {
  root = path.join(HOME, ".config", "opencode", "skills");
}

const dest = path.join(root, SKILL_NAME);

// Check if destination exists (or is a symlink)
let exists = false;
try {
  fs.lstatSync(dest);
  exists = true;
} catch (e) {
  exists = false;
}

if (exists) {
  if (!has("--force")) {
    console.error(`\x1b[31mTarget already exists: ${dest}\x1b[0m`);
    console.error("Use --force to overwrite.");
    process.exit(1);
  }
  try {
    fs.unlinkSync(dest);
  } catch (e) {
    fs.rmSync(dest, { recursive: true, force: true });
  }
}

fs.mkdirSync(root, { recursive: true });

// Source to link / copy: prefer REPO_ROOT (so SKILL.md, board/, etc. are all present) or SKILL_DIR
// If SKILL_DIR exists with SKILL.md and board/, use SKILL_DIR, else link REPO_ROOT
const srcToUse = fs.existsSync(path.join(SKILL_DIR, "SKILL.md"))
  ? SKILL_DIR
  : REPO_ROOT;

if (has("--copy")) {
  fs.cpSync(srcToUse, dest, { recursive: true });
  console.log(`\x1b[32m✔ Copied ${SKILL_NAME} skill → ${dest}\x1b[0m`);
} else {
  fs.symlinkSync(srcToUse, dest, "dir");
  console.log(
    `\x1b[32m✔ Symlinked ${SKILL_NAME} skill → ${dest} -> ${srcToUse}\x1b[0m`,
  );
}

// Ensure execution permissions on scripts
const scripts = [
  path.join(dest, "board", "run-sandboxed.sh"),
  path.join(dest, "board", "serve.py"),
  path.join(REPO_ROOT, "board", "run-sandboxed.sh"),
  path.join(REPO_ROOT, "board", "serve.py"),
];

for (const script of scripts) {
  if (fs.existsSync(script)) {
    try {
      fs.chmodSync(script, 0o755);
    } catch (e) {
      // Ignore in restricted environments
    }
  }
}

console.log(`\x1b[1;32m🎉 agent-board skill installed successfully!\x1b[0m\n`);
