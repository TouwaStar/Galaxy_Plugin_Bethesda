import re

AUTH_START_URL = r"https://bethesda.net/en/dashboard?cogs_modal=login"
AUTH_FINISH_URL = r"cogs_modal"
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
    "start_uri": AUTH_START_URL,
    "end_uri_regex": regex_pattern(AUTH_REDIRECT_URL)
}

JS = {
    regex_pattern(AUTH_FINISH_URL): [
        r'''
            function getCookie(name) {
                const value = `; ${document.cookie}`;
                const parts = value.split(`; ${name}=`);
                if (parts.length === 2) return parts.pop().split(';').shift();
            }

            function check_login() {
                if (getCookie('bnet-username')) {
                    location.href = '%s';
                }
            }

            setInterval(check_login, 3000);

            function findpersist() {
                if (document.getElementsByName("persist").length < 1) {
                    setTimeout(findpersist, 500); // give everything some time to render
                } else {
                    document.getElementsByName("persist")[0].click();
                }
            }
            findpersist();
        ''' % AUTH_REDIRECT_URL
    ]
}