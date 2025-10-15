# EmulatorJS Setup

EmulatorJS is a web-based multi-system emulator that supports various consoles.

## Installation

To use EmulatorJS, you can either:

### Option 1: CDN (Recommended for quick setup)
The emulator templates already use CDN links, so no additional setup is needed.

### Option 2: Self-hosted
1. Download EmulatorJS from https://github.com/EmulatorJS/EmulatorJS
2. Extract files to this directory
3. Update the script paths in the emulator templates to use local files

## Supported Systems

EmulatorJS supports:
- NES (Nintendo Entertainment System)
- SNES (Super Nintendo)
- Game Boy / Game Boy Color
- Game Boy Advance
- Nintendo 64
- Nintendo DS
- PlayStation
- Sega Genesis / Mega Drive
- Sega Master System
- Sega Game Gear
- Atari 2600
- Arcade (MAME)

## Usage

ROMs should be placed in the respective `/games/{console}/` directories.
Supported formats vary by system (typically .zip, .nes, .smc, .gb, .gba, .n64, .iso, etc.)
