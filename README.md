# 🏡 HomeHub: Your All-In-One Family Dashboard

> **Note:** This project was originally inspired by and based on concepts from [surajverma/homehub](https://github.com/surajverma/homehub). It has been extensively modified and customized with new features and improvements.

A simple, private spot on your home network for your family's daily needs. HomeHub is a lightweight, self-hosted web app that turns any computer (even a Raspberry Pi!) into a central hub for shared notes, shopping lists, chores, a media downloader, games, and more.

## ✨ What Can It Do?

HomeHub is packed with useful tools to make family life a little more organized:

* **📝 Shared Notes**: A simple place to jot down quick notes for everyone to see.
* **☁️ Shared Cloud**: Easily upload and share files across your home network.
* **🛒 Shopping List**: A collaborative list so you never forget the milk again. Comes with suggestions based on your history!
* **✅ Chore Tracker**: A simple to-do list for household tasks.
* **🗓️ Calendar & Reminders**: A shared calendar to keep track of important dates.
* **👋 Who's Home?**: See at a glance who is currently home.
* **💰 Expense Tracker**: Track family spending, with support for recurring bills.
* **🔐 Password Manager**: Integrated Bitwarden/Vaultwarden for secure password management.
* **🎬 Media Downloader**: Save videos or music from popular sites directly to your server.
* **♟️ Chess Game**: Play chess against AI (5 difficulty levels), local 2-player, or **remote multiplayer** with shareable links!
* **🎮 Arcade Games**: Add your own HTML5 games to the games directory.
* ...and more, including a **Recipe Book**, **Expiry Tracker**, **URL Shortener**, **PDF Compressor**, and **QR Code Generator**!

## 🎯 Key Features

* **Private & Self-Hosted**: All your data stays on your network. No cloud, no tracking.
* **Simple & Lightweight**: Runs smoothly on minimal hardware.
* **Family-Focused**: Designed to be intuitive for users of all technical skill levels.
* **Customizable**: Toggle features on or off and change the color theme from the `config.yml` file.
* **Secure**: User authentication with password hashing, session management, and per-user data isolation.

## 🚀 Getting Started

### Prerequisites
- Docker and Docker Compose installed
- A local network to run on

### Installation

**1. Clone or download this repository:**
```bash
git clone <your-repo-url>
cd homehub
```

**2. Create your configuration files:**

Copy the example files and customize them:
```bash
cp config.yml.example config.yml
cp .env.example .env
```

**3. Edit `config.yml`:**

Set your instance name, family members, and enabled features:
```yaml
instance_name: "My Home Hub"
admin_name: "Administrator"
family_members:
  - Mom
  - Dad
  - Alice
  - Bob
feature_toggles:
  shopping_list: true
  media_downloader: true
  # ... enable/disable features as needed
```

**4. Edit `.env` file:**

**IMPORTANT:** Update the SECRET_KEY for production:
```bash
# Generate a secure secret key:
python3 -c "import secrets; print(secrets.token_hex(32))"

# Add it to your .env file:
SECRET_KEY=your_generated_key_here
```

Also update Vaultwarden URLs if you're using the password manager:
```bash
VAULTWARDEN_URL=http://homehub-vaultwarden:80
VAULTWARDEN_DOMAIN=http://localhost:8080
```

**5. Start the services:**
```bash
docker compose up -d
```

**6. Access HomeHub:**

Open your browser and go to: **http://localhost:8765**

### First Run - Setting Up Passwords

When you first access HomeHub:
1. You'll be prompted to log in
2. Select your username from the dropdown (Administrator or a family member)
3. On first login, create a secure password (minimum 8 characters)
4. You'll be logged in automatically

**Administrator privileges:**
- Reset family member passwords if forgotten
- Manage user accounts
- Access via sidebar: "Admin: Reset Passwords"

### 🔐 Security Notes

- **Change the SECRET_KEY**: Never use the default! Generate a random one using the command above.
- **Passwords are hashed**: Never stored in plain text (uses werkzeug password hashing).
- **Sessions expire**: Automatic 24-hour session timeout.
- **Data isolation**: Users can only access their own data.
- **Sensitive files ignored**: `.gitignore` prevents committing passwords, keys, or user data.

### 🎮 Chess Game - Remote Multiplayer

The chess feature supports:
- **AI Mode**: Play against computer with 5 difficulty levels (Beginner to Expert)
- **2-Player Local**: Play on the same device
- **2-Player Remote**: Generate a shareable link for remote opponents
  - Player 1 (White) creates game and shares link
  - Player 2 (Black) joins via link without authentication
  - Both players see board from their perspective
  - "Complete My Move" button system for turn confirmation

## 🎨 Theming

HomeHub follows your system dark/light mode. Customize colors in `config.yml`:

```yaml
theme:
  primary_color: "#1d4ed8"
  sidebar_background_color: "#2563eb"
  sidebar_text_color: "#ffffff"
  # ... more theme options
```

## 🔧 Development Setup

### Local Development (without Docker)

1. **Clone and setup Python environment:**
```bash
python -m venv venv
source venv/bin/activate  # On Linux/Mac
# or
venv\Scripts\activate  # On Windows
pip install -r requirements.txt
```

2. **Setup configuration:**
```bash
cp config.yml.example config.yml
# Edit config.yml with your settings
```

3. **Build CSS (Tailwind):**
```bash
npm install
npm run build:css
```

For live CSS rebuilds during development:
```bash
npm run watch:css
```

4. **Run the application:**
```bash
python run.py
```

Access at: http://localhost:5000

### Docker Development

To rebuild after changes:
```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

## 📁 Project Structure

```
homehub/
├── app/                    # Flask application
│   ├── routes.py          # Main routes and logic
│   └── ...
├── templates/             # HTML templates (Jinja2)
├── static/               # CSS, JS, images
├── games/                # HTML5 games directory
├── data/                 # SQLite database (gitignored)
├── uploads/              # User uploads (gitignored)
├── config.yml            # Configuration (gitignored)
├── .env                  # Environment variables (gitignored)
├── compose.yml           # Docker Compose configuration
├── Dockerfile            # Docker build instructions
└── requirements.txt      # Python dependencies
```

## 🤝 Contributing

Contributions are welcome! If you have ideas, suggestions, or bug reports:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## 📝 License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## 🙏 Attribution

This project was originally inspired by [surajverma/homehub](https://github.com/surajverma/homehub). Special thanks to the original author for the initial concept and foundation.

## ⚠️ Important Reminders

- **Never commit** `config.yml`, `.env`, or data directories to version control
- **Always change** the SECRET_KEY in production
- **Backup regularly**: Your data directory contains all user data
- **Keep updated**: Pull latest security updates regularly

---

**Enjoy your private family hub!** 🏡
