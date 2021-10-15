import re

AUTH_START_URL = r"https://bethesda.net/en/dashboard?cogs_modal=login"
AUTH_FINISH_URL = r"cogs_modal"
AUTH_REDIRECT_URL = r"radiant/v1/graphql"
AUTH_CHECK_URL = r"https://api.bethesda.net/dwemer/attunement/v1/authenticate"
API_URL = r"https://api.bethesda.net"

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
            function checkLogin() {
                makeLoginRequest(() => { location.href = '%s'; })
            }

            function makeLoginRequest(callback) {
                var xhr = new XMLHttpRequest();
                xhr.onreadystatechange = function() { 
                    if (xhr.readyState == XMLHttpRequest.DONE && xhr.status >= 200 && xhr.status < 300) {
                        callback();
                    }
                }
                xhr.open('PUT', '%s', true);
                xhr.withCredentials = true;
                xhr.send(null);
            }

            // Catch all requests and check if auth is successful after any API request
            var origOpen = XMLHttpRequest.prototype.open;
            XMLHttpRequest.prototype.open = function() {
                this.addEventListener('load', async function() {
                    if (this.responseURL.indexOf('%s') >= 0) {
                        checkLogin();
                    }
                });
                origOpen.apply(this, arguments);
            };

            function findpersist() {
                if (document.getElementsByName("persist").length < 1) {
                    setTimeout(findpersist, 500); // give everything some time to render
                } else {
                    document.getElementsByName("persist")[0].click();
                }
            }
            findpersist();
        ''' % (AUTH_REDIRECT_URL, AUTH_CHECK_URL, API_URL)
    ]
}
