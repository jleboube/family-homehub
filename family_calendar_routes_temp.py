# Family Calendar Setup (for non-admin family members)
@main_bp.route('/caldav/family-setup')
def family_calendar_setup():
    """Setup page for family members to add shared calendar to their devices"""
    config = current_app.config['HOMEHUB_CONFIG']
    from flask import g

    # Skip for admin (they use the main caldav setup page)
    if hasattr(g, 'current_user') and g.current_user.is_admin:
        flash('Administrators should use the Calendar setup page from the sidebar.', 'info')
        return redirect(url_for('main.caldav_setup'))

    username = session.get('username', '')

    # Detect current host and protocol
    detected_host = request.host.split(':')[0] if request.host else 'localhost'
    is_https = request.headers.get('X-Forwarded-Proto', 'http') == 'https' or request.is_secure
    protocol = 'https' if is_https else 'http'

    # CalDAV server configuration for family-viewer account
    if 'my-house.dev' in detected_host:
        server_host = 'calendar.my-house.dev'
        server_port = ''
        caldav_url = f"{protocol}://{server_host}/family-viewer/homehub-calendar/"
    else:
        server_host = detected_host
        server_port = '5232'
        caldav_url = f"{protocol}://{server_host}:{server_port}/family-viewer/homehub-calendar/"

    # Get family calendar password from environment
    family_password = os.environ.get('FAMILY_CALDAV_PASSWORD', 'family2024')

    return render_template('family_calendar_setup.html',
                         config=config,
                         username=username,
                         caldav_url=caldav_url,
                         caldav_username='family-viewer',
                         caldav_password=family_password,
                         server_host=server_host,
                         server_port=server_port)


@main_bp.route('/caldav/family-calendar.mobileconfig')
def download_family_calendar_profile():
    """Generate and download iOS configuration profile for family calendar"""
    username = session.get('username', 'User')

    # Detect current host and protocol
    detected_host = request.host.split(':')[0] if request.host else 'localhost'
    is_https = request.headers.get('X-Forwarded-Proto', 'http') == 'https' or request.is_secure
    protocol = 'https' if is_https else 'http'

    # CalDAV server configuration
    if 'my-house.dev' in detected_host:
        server_host = 'calendar.my-house.dev'
        server_port = ''
        server_url = f"{protocol}://{server_host}"
        caldav_path = "/family-viewer/homehub-calendar/"
    else:
        server_host = detected_host
        server_port = '5232'
        server_url = f"{protocol}://{server_host}:{server_port}"
        caldav_path = "/family-viewer/homehub-calendar/"

    # Get family calendar password
    family_password = os.environ.get('FAMILY_CALDAV_PASSWORD', 'family2024')

    # Generate unique ID for this profile
    import uuid
    profile_uuid = str(uuid.uuid4()).upper()
    caldav_uuid = str(uuid.uuid4()).upper()

    # Create iOS configuration profile (XML format)
    profile_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>PayloadContent</key>
    <array>
        <dict>
            <key>CalDAVAccountDescription</key>
            <string>Family Calendar (Read-Only)</string>
            <key>CalDAVHostName</key>
            <string>{server_host}</string>
            <key>CalDAVPort</key>
            <integer>{server_port if server_port else '443' if is_https else '80'}</integer>
            <key>CalDAVPrincipalURL</key>
            <string>{server_url}{caldav_path}</string>
            <key>CalDAVUseSSL</key>
            {('<true/>' if is_https else '<false/>')}
            <key>CalDAVUsername</key>
            <string>family-viewer</string>
            <key>CalDAVPassword</key>
            <string>{family_password}</string>
            <key>PayloadDescription</key>
            <string>Configures CalDAV account for Family Calendar</string>
            <key>PayloadDisplayName</key>
            <string>Family Calendar</string>
            <key>PayloadIdentifier</key>
            <string>com.homehub.caldav.{caldav_uuid}</string>
            <key>PayloadType</key>
            <string>com.apple.caldav.account</string>
            <key>PayloadUUID</key>
            <string>{caldav_uuid}</string>
            <key>PayloadVersion</key>
            <integer>1</integer>
        </dict>
    </array>
    <key>PayloadDisplayName</key>
    <string>HomeHub Family Calendar</string>
    <key>PayloadIdentifier</key>
    <string>com.homehub.profile.{profile_uuid}</string>
    <key>PayloadRemovalDisallowed</key>
    <false/>
    <key>PayloadType</key>
    <string>Configuration</string>
    <key>PayloadUUID</key>
    <string>{profile_uuid}</string>
    <key>PayloadVersion</key>
    <integer>1</integer>
    <key>PayloadDescription</key>
    <string>Install this profile to add the HomeHub Family Calendar to your device. Configured for {username}.</string>
</dict>
</plist>'''

    # Return as downloadable file
    from flask import Response
    response = Response(profile_content, mimetype='application/x-apple-aspen-config')
    response.headers['Content-Disposition'] = 'attachment; filename=HomeHub-Family-Calendar.mobileconfig'
    return response
