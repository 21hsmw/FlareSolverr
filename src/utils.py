import json
import logging
import os
import re
import shutil
import urllib.parse
import tempfile
import asyncio
import psutil

from selenium.webdriver.chrome.webdriver import WebDriver
import undetected_chromedriver as uc
import nodriver as nd

FLARESOLVERR_VERSION = None
DRIVER_SELECTION = None
PLATFORM_VERSION = None
CHROME_EXE_PATH = None
CHROME_MAJOR_VERSION = None
USER_AGENT = None
XVFB_DISPLAY = None
PATCHED_DRIVER_PATH = None


def get_config_log_html() -> bool:
    return os.environ.get('LOG_HTML', 'false').lower() == 'true'


def get_config_headless() -> bool:
    return os.environ.get('HEADLESS', 'true').lower() == 'true'


def get_flaresolverr_version() -> str:
    global FLARESOLVERR_VERSION
    if FLARESOLVERR_VERSION is not None:
        return FLARESOLVERR_VERSION

    package_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, 'package.json')
    if not os.path.isfile(package_path):
        package_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'package.json')
    with open(package_path) as f:
        FLARESOLVERR_VERSION = json.loads(f.read())['version']
        return FLARESOLVERR_VERSION

def get_driver_selection() -> str:
    global DRIVER_SELECTION
    if DRIVER_SELECTION is not None:
        return DRIVER_SELECTION
    DRIVER_SELECTION = os.environ.get('DRIVER', 'undetected-chromedriver')
    return DRIVER_SELECTION

def get_current_platform() -> str:
    global PLATFORM_VERSION
    if PLATFORM_VERSION is not None:
        return PLATFORM_VERSION
    PLATFORM_VERSION = os.name
    return PLATFORM_VERSION


def create_proxy_extension(proxy: dict) -> str:
    parsed_url = urllib.parse.urlparse(proxy['url'])
    scheme = parsed_url.scheme
    host = parsed_url.hostname
    port = parsed_url.port
    username = proxy['username']
    password = proxy['password']
    manifest_json = """
    {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Chrome Proxy",
        "permissions": [
            "proxy",
            "tabs",
            "unlimitedStorage",
            "storage",
            "<all_urls>",
            "webRequest",
            "webRequestBlocking"
        ],
        "background": {"scripts": ["background.js"]},
        "minimum_chrome_version": "76.0.0"
    }
    """

    background_js = """
    var config = {
        mode: "fixed_servers",
        rules: {
            singleProxy: {
                scheme: "%s",
                host: "%s",
                port: %d
            },
            bypassList: ["localhost"]
        }
    };

    chrome.proxy.settings.set({value: config, scope: "regular"}, function() {});

    function callbackFn(details) {
        return {
            authCredentials: {
                username: "%s",
                password: "%s"
            }
        };
    }

    chrome.webRequest.onAuthRequired.addListener(
        callbackFn,
        { urls: ["<all_urls>"] },
        ['blocking']
    );
    """ % (
        scheme,
        host,
        port,
        username,
        password
    )

    proxy_extension_dir = tempfile.mkdtemp()

    with open(os.path.join(proxy_extension_dir, "manifest.json"), "w") as f:
        f.write(manifest_json)

    with open(os.path.join(proxy_extension_dir, "background.js"), "w") as f:
        f.write(background_js)

    return proxy_extension_dir

def create_cloudflare_extension() -> str:
    manifest_json = """
    {
        "manifest_version": 3,
        "name": "Turnstile Patcher",
        "version": "2.1",
        "content_scripts": [
            {
                "js": [
                    "./script.js"
                ],
                "matches": [
                    "<all_urls>"
                ],
                "run_at": "document_start",
                "all_frames": true,
                "world": "MAIN"
            }
        ]
    }
    """

    script_js = """
    Object.defineProperty(MouseEvent.prototype, 'screenX', {
        get: function () {
            return this.clientX + window.screenX;
        }
    });

    Object.defineProperty(MouseEvent.prototype, 'screenY', {
        get: function () {
            return this.clientY + window.screenY;
        }
    });
    """

    cloudflare_extension_dir = tempfile.mkdtemp()

    with open(os.path.join(cloudflare_extension_dir, "manifest.json"), "w") as f:
        f.write(manifest_json)

    with open(os.path.join(cloudflare_extension_dir, "script.js"), "w") as f:
        f.write(script_js)

    return cloudflare_extension_dir

async def get_webdriver_nd(proxy: dict = None) -> nd.Browser:
    logging.debug('Launching web browser with nodriver...')

    options = nd.Config()
    options.sandbox = False
    options.add_argument('--window-size=1920,1080')
    # todo: this param shows a warning in chrome head-full
    options.add_argument('--disable-setuid-sandbox')
    # this option removes the zygote sandbox (it seems that the resolution is a bit faster)
    options.add_argument('--no-zygote')
    # attempt to fix Docker ARM32 build
    options.add_argument('--disable-gpu-sandbox')
    options.add_argument('--disable-software-rasterizer')
    options.add_argument('--ignore-certificate-errors')
    options.add_argument('--ignore-ssl-errors')
    # fix GL errors in ASUSTOR NAS
    # https://github.com/FlareSolverr/FlareSolverr/issues/782
    # https://github.com/microsoft/vscode/issues/127800#issuecomment-873342069
    # https://peter.sh/experiments/chromium-command-line-switches/#use-gl
    options.add_argument('--use-gl=swiftshader')

    language = os.environ.get('LANG', None)
    if language is not None:
        options.lang = language
    else:
        options.lang = "en-US"

    # Fix for Chrome 117 | https://github.com/FlareSolverr/FlareSolverr/issues/910
    if USER_AGENT is not None:
        options.add_argument('--user-agent=%s' % USER_AGENT)

    proxy_extension_dir = None
    if proxy and all(key in proxy for key in ['url', 'username', 'password']):
        proxy_extension_dir = create_proxy_extension(proxy)
        options.add_extension(os.path.abspath(proxy_extension_dir))
    elif proxy and 'url' in proxy:
        proxy_url = proxy['url']
        logging.debug("Using proxy: %s", proxy_url)
        options.add_argument('--proxy-server=%s' % proxy_url)

    # add cloudflare addon
    # https://github.com/TheFalloutOf76/CDP-bug-MouseEvent-.screenX-.screenY-patcher
    cloudflare_extension_dir = create_cloudflare_extension()
    options.add_extension(os.path.abspath(cloudflare_extension_dir))

    # note: headless mode is detected (headless = True)
    # we launch the browser in head-full mode with the window hidden
    if get_config_headless():
        if PLATFORM_VERSION == 'nt':
            options.windows_headless = True
        else:
            start_xvfb_display()
    # For normal headless mode:
    # options.headless = True or False

    # Add browser binary path for Windows
    if PLATFORM_VERSION == "nt":
        options.browser_executable_path = CHROME_EXE_PATH

    try:
        driver = await nd.Browser.create(config=options)
    except Exception as e:
        logging.error("Error creating Chrome Browser: %s" % e)

    # clean up proxy extension directory
    if proxy_extension_dir is not None:
        shutil.rmtree(proxy_extension_dir)

    return driver


def get_webdriver_uc(proxy: dict = None) -> WebDriver:
    global PATCHED_DRIVER_PATH, USER_AGENT

    logging.debug('Launching web browser with undetected-chromedriver...')

    # undetected_chromedriver
    options = uc.ChromeOptions()
    options.add_argument('--no-sandbox')
    options.add_argument('--window-size=1920,1080')
    # todo: this param shows a warning in chrome head-full
    options.add_argument('--disable-setuid-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    # this option removes the zygote sandbox (it seems that the resolution is a bit faster)
    options.add_argument('--no-zygote')
    # attempt to fix Docker ARM32 build
    options.add_argument('--disable-gpu-sandbox')
    options.add_argument('--disable-software-rasterizer')
    options.add_argument('--ignore-certificate-errors')
    options.add_argument('--ignore-ssl-errors')
    # fix GL errors in ASUSTOR NAS
    # https://github.com/FlareSolverr/FlareSolverr/issues/782
    # https://github.com/microsoft/vscode/issues/127800#issuecomment-873342069
    # https://peter.sh/experiments/chromium-command-line-switches/#use-gl
    options.add_argument('--use-gl=swiftshader')

    language = os.environ.get('LANG', None)
    if language is not None:
        options.add_argument('--accept-lang=%s' % language)

    # Fix for Chrome 117 | https://github.com/FlareSolverr/FlareSolverr/issues/910
    if USER_AGENT is not None:
        options.add_argument('--user-agent=%s' % USER_AGENT)

    proxy_extension_dir = None
    if proxy and all(key in proxy for key in ['url', 'username', 'password']):
        proxy_extension_dir = create_proxy_extension(proxy)
        options.add_argument("--load-extension=%s" % os.path.abspath(proxy_extension_dir))
    elif proxy and 'url' in proxy:
        proxy_url = proxy['url']
        logging.debug("Using webdriver proxy: %s", proxy_url)
        options.add_argument('--proxy-server=%s' % proxy_url)

    # note: headless mode is detected (headless = True)
    # we launch the browser in head-full mode with the window hidden
    windows_headless = False
    if get_config_headless():
        if PLATFORM_VERSION == 'nt':
            windows_headless = True
        else:
            start_xvfb_display()
    # For normal headless mode:
    # options.add_argument('--headless')

    options.add_argument("--auto-open-devtools-for-tabs")
    options.add_argument("--disable-popup-blocking")

    # if we are inside the Docker container, we avoid downloading the driver
    driver_exe_path = None
    version_main = None
    if os.path.exists("/app/chromedriver"):
        # running inside Docker
        driver_exe_path = "/app/chromedriver"
    else:
        version_main = get_chrome_major_version()
        if PATCHED_DRIVER_PATH is not None:
            driver_exe_path = PATCHED_DRIVER_PATH

    # detect chrome path
    browser_executable_path = get_chrome_exe_path()

    # downloads and patches the chromedriver
    # if we don't set driver_executable_path it downloads, patches, and deletes the driver each time
    try:
        driver = uc.Chrome(options=options, browser_executable_path=browser_executable_path,
                           driver_executable_path=driver_exe_path, version_main=version_main,
                           windows_headless=windows_headless, headless=get_config_headless())
    except Exception as e:
        logging.error("Error starting Chrome: %s" % e)

    # save the patched driver to avoid re-downloads
    if driver_exe_path is None:
        PATCHED_DRIVER_PATH = os.path.join(driver.patcher.data_path, driver.patcher.exe_name)
        if PATCHED_DRIVER_PATH != driver.patcher.executable_path:
            shutil.copy(driver.patcher.executable_path, PATCHED_DRIVER_PATH)

    # clean up proxy extension directory
    if proxy_extension_dir is not None:
        shutil.rmtree(proxy_extension_dir)

    # selenium vanilla
    # options = webdriver.ChromeOptions()
    # options.add_argument('--no-sandbox')
    # options.add_argument('--window-size=1920,1080')
    # options.add_argument('--disable-setuid-sandbox')
    # options.add_argument('--disable-dev-shm-usage')
    # driver = webdriver.Chrome(options=options)

    return driver


def get_chrome_exe_path() -> str:
    global CHROME_EXE_PATH
    if CHROME_EXE_PATH is not None:
        return CHROME_EXE_PATH
    # linux pyinstaller bundle
    chrome_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chrome', "chrome")
    if os.path.exists(chrome_path):
        if not os.access(chrome_path, os.X_OK):
            raise Exception(f'Chrome binary "{chrome_path}" is not executable. '
                            f'Please, extract the archive with "tar xzf <file.tar.gz>".')
        CHROME_EXE_PATH = chrome_path
        return CHROME_EXE_PATH
    # windows pyinstaller bundle
    chrome_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chrome', "chrome.exe")
    if os.path.exists(chrome_path):
        CHROME_EXE_PATH = chrome_path
        return CHROME_EXE_PATH
    # system
    CHROME_EXE_PATH = uc.find_chrome_executable()
    return CHROME_EXE_PATH


def get_chrome_major_version() -> str:
    global CHROME_MAJOR_VERSION
    if CHROME_MAJOR_VERSION is not None:
        return CHROME_MAJOR_VERSION

    if PLATFORM_VERSION == 'nt':
        # Example: '104.0.5112.79'
        try:
            complete_version = extract_version_nt_executable(get_chrome_exe_path())
        except Exception:
            try:
                complete_version = extract_version_nt_registry()
            except Exception:
                # Example: '104.0.5112.79'
                complete_version = extract_version_nt_folder()
    else:
        chrome_path = get_chrome_exe_path()
        process = os.popen(f'"{chrome_path}" --version')
        # Example 1: 'Chromium 104.0.5112.79 Arch Linux\n'
        # Example 2: 'Google Chrome 104.0.5112.79 Arch Linux\n'
        complete_version = process.read()
        process.close()

    CHROME_MAJOR_VERSION = complete_version.split('.')[0].split(' ')[-1]
    return CHROME_MAJOR_VERSION


def extract_version_nt_executable(exe_path: str) -> str:
    import pefile
    pe = pefile.PE(exe_path, fast_load=True)
    pe.parse_data_directories(
        directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]]
    )
    return pe.FileInfo[0][0].StringTable[0].entries[b"FileVersion"].decode('utf-8')


def extract_version_nt_registry() -> str:
    stream = os.popen(
        'reg query "HKLM\\SOFTWARE\\Wow6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Google Chrome"')
    output = stream.read()
    google_version = ''
    for letter in output[output.rindex('DisplayVersion    REG_SZ') + 24:]:
        if letter != '\n':
            google_version += letter
        else:
            break
    return google_version.strip()


def extract_version_nt_folder() -> str:
    # Check if the Chrome folder exists in the x32 or x64 Program Files folders.
    for i in range(2):
        path = 'C:\\Program Files' + (' (x86)' if i else '') + '\\Google\\Chrome\\Application'
        if os.path.isdir(path):
            paths = [f.path for f in os.scandir(path) if f.is_dir()]
            for path in paths:
                filename = os.path.basename(path)
                pattern = '\d+\.\d+\.\d+\.\d+'
                match = re.search(pattern, filename)
                if match and match.group():
                    # Found a Chrome version.
                    return match.group(0)
    return ''


async def get_user_agent_nd(driver=None) -> str:
    global USER_AGENT
    if USER_AGENT is not None:
        return USER_AGENT

    try:
        if driver is None:
            logging.info("Launching web browser...")
            driver = await get_webdriver_nd()
        USER_AGENT = driver.info['User-Agent']
        # Fix for Chrome 117 | https://github.com/FlareSolverr/FlareSolverr/issues/910
        USER_AGENT = re.sub('HEADLESS', '', USER_AGENT, flags=re.IGNORECASE)
        return USER_AGENT
    except Exception as e:
        raise Exception("Error getting browser User-Agent. " + str(e))
    finally:
        if driver is not None:
            await after_run_cleanup(driver=driver)


def get_user_agent_uc(driver=None) -> str:
    global USER_AGENT
    if USER_AGENT is not None:
        return USER_AGENT

    try:
        if driver is None:
            driver = get_webdriver_uc()
        USER_AGENT = driver.execute_script("return navigator.userAgent")
        # Fix for Chrome 117 | https://github.com/FlareSolverr/FlareSolverr/issues/910
        USER_AGENT = re.sub('HEADLESS', '', USER_AGENT, flags=re.IGNORECASE)
        return USER_AGENT
    except Exception as e:
        raise Exception("Error getting browser User-Agent. " + str(e))
    finally:
        if driver is not None:
            if PLATFORM_VERSION == "nt":
                driver.close()
            driver.quit()


async def after_run_cleanup(driver: nd.Browser):
    """
    After run function to remove Chromium processes and delete the
    the Browser instance data dir if necessary.
    """

    # Get Browser instance process
    process = driver.get_process
    if process is None:
        return

    # Get the list of child processes before closing the Browser instance
    child_processes = psutil.Process(process.pid).children(recursive=True)

    # Stop Browser instance
    driver.stop()

    # Wait for the websocket to return True (Closed)
    while True:
        websocket_status = driver.connection.closed
        logging.debug(f"Websocket status: {websocket_status}")
        if websocket_status:
            break
        await asyncio.sleep(0.1)

    # Find all chromium processes and terminate them if any
    for proc in child_processes:
        try:
            if proc.pid == process.pid:
                logging.debug(f"Terminating Chromium process with PID: {proc.pid}")
                proc.terminate()
            elif any(name in proc.name().lower() for name in ("chromium", "chrome")):
                logging.debug(f"Terminating Chromium child process with PID: {proc.pid}")
                proc.terminate()
            elif proc.status() == 'zombie':
                logging.debug(f"Terminating zombie Chromium process with PID: {proc.pid}")
                proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    # Wait for all processes to terminate
    for proc in child_processes:
        try:
            if proc.pid == process.pid or \
            any(name in proc.name().lower() for name in ("chromium", "chrome")):
                proc.wait(timeout=10)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    # Delete Browser instance data dir
    try:
        user_dir = driver.config.user_data_dir
        shutil.rmtree(user_dir, ignore_errors=False)
        logging.debug(f"Removed Browser user data directory {user_dir}")
    except OSError as e:
        logging.debug(f"Failed to delete Browser user data directory {user_dir} - {str(e)}")

    # Remove Browser instance from created instances
    try:
        nd.util.get_registered_instances().remove(driver)
    except Exception as e:
        logging.debug(f"Error when removing the Browser instance: {str(e)}")

def start_xvfb_display():
    global XVFB_DISPLAY
    if XVFB_DISPLAY is None:
        from xvfbwrapper import Xvfb
        XVFB_DISPLAY = Xvfb()
        XVFB_DISPLAY.start()
        logging.debug("VIRTUAL SCREEN STARTED")


def object_to_dict(_object):
    json_dict = json.loads(json.dumps(_object, default=lambda o: o.__dict__))
    # remove hidden fields
    return {k: v for k, v in json_dict.items() if not k.startswith('__')}
