# Games Directory

This directory contains browser-playable games for the HomeHub Games page.

## Directory Structure

### HTML5 Games
- **html5/** - Browser-based HTML5 games (no emulator required)

### Console Emulators (powered by EmulatorJS)
- **nes/** - Nintendo Entertainment System (.nes, .zip)
- **snes/** - Super Nintendo Entertainment System (.smc, .sfc, .zip)
- **gameboy/** - Game Boy / Game Boy Color (.gb, .gbc, .zip)
- **gba/** - Game Boy Advance (.gba, .zip)
- **n64/** - Nintendo 64 (.n64, .z64, .v64, .zip)
- **sega/** - Sega Genesis / Mega Drive (.md, .bin, .zip)
- **playstation/** - Sony PlayStation (.iso, .bin/.cue, .chd)
- **arcade/** - MAME Arcade games (.zip)

## Legal Notice

**IMPORTANT**: This system provides emulator infrastructure only. You are responsible for ensuring you have the legal right to use any ROM files or game content you add to these directories. Only use:

1. ROM files you have legally obtained from games you own
2. Homebrew games
3. Games explicitly licensed for free distribution
4. Open-source games

Nintendo, PlayStation, Sega, and other console games are copyrighted material. Distributing or using unauthorized copies is illegal.

## Adding Games

### HTML5 Games
Place HTML5 game files or folders in the `html5/` directory. For multi-file games, create a folder with an `index.html` entry point.

Example:
```
html5/
  ├── snake-game.html
  ├── pong.html
  └── my-game/
      ├── index.html
      ├── game.js
      └── style.css
```

### Console ROMs
Place ROM files directly in their respective console directories. The emulator will automatically detect and display them.

Example:
```
nes/
  ├── homebrew-game.nes
  └── another-game.zip

gameboy/
  └── my-gb-game.gb
```

## Resources

### Legal Homebrew ROMs
- **PDRoms**: https://pdroms.de/
- **NESdev Homebrew**: https://nesdev.org/homebrew_games.html
- **Zophar's Domain**: https://www.zophar.net/pdroms.html

### HTML5 Games
- **itch.io Open Source**: https://itch.io/games/free/tag-open-source
- **LibreGameWiki**: https://libregamewiki.org/Main_Page
- **Free Game Dev**: https://freegamedev.net/

## Emulator Details

This installation uses **EmulatorJS** (https://github.com/EmulatorJS/EmulatorJS), a web-based multi-system emulator. The emulator runs entirely in the browser using JavaScript, with no server-side processing required.

### Supported File Formats

Most emulators support both raw ROM files and compressed .zip files. For PlayStation games, you may need BIOS files for some games (place in the respective console directory).

### Controls

Default emulator controls:
- **Arrow Keys**: D-Pad
- **Z/X/A/S**: Action buttons (A/B/X/Y)
- **Enter**: Start
- **Shift**: Select
- **F11**: Fullscreen
