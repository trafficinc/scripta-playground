![Scripta Playground. A local browser playground with a Scripta editor and console.](docs/assets/readme-header.svg)

# Scripta Playground

A local browser playground for [Scripta](https://github.com/trafficinc/scripta),
with a syntax-colored editor, examples, and a console. It is distributed
separately from the interpreter and works offline.

## Quick start from a Git clone

Install Rust through rustup, Python 3.9+, and native C build tools (on macOS,
run `xcode-select --install`). Clone both repositories into sibling folders,
then build:

```sh
git clone https://github.com/trafficinc/scripta.git
git clone https://github.com/trafficinc/scripta-playground.git
cd scripta-playground
python3 scripts/build-standalone.py
```

If you already cloned this repository, clone Scripta beside it with
`git clone https://github.com/trafficinc/scripta.git ../scripta` from the
playground folder, then run the build command above.

The build prints the executable path for your platform. On Apple Silicon
macOS 15+, launch it with:

```sh
./dist/standalone-aarch64-apple-darwin/scr-playground
```

It opens your browser automatically. Keep the terminal running while you use
the playground; press Ctrl+C to stop it.
Use `--scripta-source /path/to/scripta` when building with an interpreter source
checkout in another location. Only the Apple Silicon macOS build has been
tested so far.

## Download and launch

For a standalone executable, run `scr-playground` (`scr-playground.exe` on
Windows). It opens your browser, uses a compatible installed Scripta when
available, and otherwise runs its embedded interpreter. No separate installation
is required.

When standalone releases are available, download the archive for your operating
system and processor from this repository's Releases page. Extract it, then launch:

- macOS: double-click `Start Playground.command`.
- Windows: double-click `Start Playground.cmd`.
- Linux: run `./start-playground.sh`.

The download bundles Scripta; no development tools or separate interpreter are
needed. The launcher opens your browser and prints a fallback URL. Quit from the
page or press Ctrl+C in the terminal; closing the tab leaves the server running.
Downloaded executables are unsigned, so platform prompts may apply.

## Use an existing interpreter

Put `scr-playground` (`scr-playground.exe` on Windows) beside `scripta`, or add the
playground folder to PATH. If your interpreter includes the playground launcher:

```sh
scripta playground
```

Or launch the companion directly:

```sh
scr-playground --scr /path/to/scripta
```

## Features and safety

Edit and run `.scripta` files with syntax highlighting, examples, Run/Stop controls,
output and error tabs, and Ctrl/Cmd+Enter. Import scripts or download your work;
all browser assets are bundled locally.

To add your own examples, create an `app_scripts/` folder beside the
`scr-playground` executable and drop `.scripta` files into it. Refresh the page
to see them under **Your scripts** (or in the example menu on mobile). Selecting
a script opens it in the editor; use **Run script** to execute it. Files must be
UTF-8, no larger than 256 KiB, and directly inside the folder. **Save script**
downloads your edits; it does not overwrite the original file.

Standalone builds create this folder automatically and copy `.scripta` files
from the repository's `app_scripts/` into it, so you can include your own
advanced examples when building.

Run only trusted scripts: they have your account's filesystem, network, and
process permissions. The server listens on localhost; do not proxy or expose it
publicly. Runs have time and output limits, but execution is not sandboxed.

## Development

See the [development guide](docs/development.md) for building from source,
interpreter configuration, testing, execution limits, and release packaging.

### Scan for secrets

```sh
brew install gitleaks
./scripts/scan-secrets.sh
```

Scans Git history; use `--help` for other modes.

### Build downloadable releases

For local builds, follow [Quick start from a Git clone](#quick-start-from-a-git-clone)
above. The build script detects your native target and bundles the interpreter.

For automated builds, open **Actions → Standalone builds and releases → Run
workflow**. Enter a version such as `0.1.0` and choose **Build only**,
**Prerelease**, or **Release** from the dropdown. Leave the Scripta ref as `main`
or select a tag/commit. The workflow must first be pushed to the default branch.

All four native builds and tests must pass before downloads are assembled or
published. Build only saves artifacts without creating a tag or release. See
[manual workflow details](docs/development.md#manual-github-actions-builds)
for versioning, downloads, and release behavior.

## License

[MIT](LICENSE). Standalone archives include bundled dependency notices.
