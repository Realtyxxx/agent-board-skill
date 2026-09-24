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

// skill/board is a relative symlink. fs.cpSync rewrites nested symlinks to
// absolute links back into this repo (its `dereference` option only covers the
// top-level source), so copy by hand, following links.
function copyDereferenced(src, dst) {
  const st = fs.statSync(src);
  if (st.isDirectory()) {
    fs.mkdirSync(dst, { recursive: true });
    for (const entry of fs.readdirSync(src)) {
      copyDereferenced(path.join(src, entry), path.join(dst, entry));
    }
  } else {
    fs.copyFileSync(src, dst);
    fs.chmodSync(dst, st.mode & 0o777);
  }
}

// Source to link / copy: prefer REPO_ROOT (so SKILL.md, board/, etc. are all present) or SKILL_DIR
// If SKILL_DIR exists with SKILL.md and board/, use SKILL_DIR, else link REPO_ROOT
const srcToUse = fs.existsSync(path.join(SKILL_DIR, "SKILL.md"))
  ? SKILL_DIR
  : REPO_ROOT;

// Real path of p, resolving symlinks in the part of it that already exists.
function realpathLoose(p) {
  const abs = path.resolve(p);
  const rest = [];
  let cur = abs;
  while (!fs.existsSync(cur)) {
    rest.unshift(path.basename(cur));
    const parent = path.dirname(cur);
    if (parent === cur) break;
    cur = parent;
  }
  return path.join(fs.realpathSync(cur), ...rest);
}

// Every real directory the copy will walk, following symlinks (skill/board
// points at ../board).
function sourceDirs(src, seen = new Set()) {
  const real = fs.realpathSync(src);
  if (seen.has(real) || !fs.statSync(real).isDirectory()) return seen;
  seen.add(real);
  for (const entry of fs.readdirSync(real)) {
    sourceDirs(path.join(real, entry), seen);
  }
  return seen;
}

// A --copy destination inside the source tree would copy itself into itself
// until ENAMETOOLONG. Check before --force deletes anything.
if (has("--copy")) {
  const dirs = sourceDirs(srcToUse);
  for (let p = realpathLoose(dest); ; p = path.dirname(p)) {
    if (dirs.has(p)) {
      console.error(`\x1b[31mCopy destination is inside the source tree: ${dest}\x1b[0m`);
      console.error("Choose a --dir outside " + fs.realpathSync(REPO_ROOT));
      process.exit(1);
    }
    if (path.dirname(p) === p) break;
  }
}

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

if (has("--copy")) {
  copyDereferenced(srcToUse, dest);
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
