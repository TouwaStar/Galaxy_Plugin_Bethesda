import re

AUTH_URL = r"https://account.bethesda.net/login"
AUTH_REDIRECT_URL = r"radiant/v1/graphql"

BETTY_WINREG_LOCATION = "SOFTWARE\\Bethesda Softworks\\Bethesda.net"
BETTY_LAUNCHER_EXE = "BethesdaNetLauncher.exe"

WINDOWS_UNINSTALL_LOCATION = "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall"


def regex_pattern(regex):
    return ".*" + re.escape(regex) + ".*"


AUTH_PARAMS = {
    "window_title": "Login to Bethesda\u2122",
    "window_width": 700,
    "window_height": 600,
    "start_uri": AUTH_URL,
    "end_uri_regex": regex_pattern(AUTH_REDIRECT_URL)
}


