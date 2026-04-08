import asyncio
import logging
import os
import time

from playwright.async_api import Browser, Page, async_playwright

logger = logging.getLogger(__name__)

SCREENSHOTS_DIR = os.getenv("SCREENSHOTS_DIR", "/workspace/screenshots")

# Chrome-флаги для WebRTC в контейнере
# НЕ используем --use-fake-device-for-media-stream — это убивает реальный аудио-выход
CHROME_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--use-fake-ui-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
    "--disable-web-security",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--window-size=1280,720",
    "--disable-features=WebRtcHideLocalIpsWithMdns",
    "--disable-blink-features=AutomationControlled",
    "--lang=ru-RU,ru",
]


class TelemostSession:
    """Управление сессией Телемоста через Playwright.

    Все клики через page.evaluate() (JS) — обходит модальные overlay Телемоста.
    Подход из рабочего hh-recruiter-bot.
    """

    def __init__(self, meeting_url: str, bot_name: str):
        self.meeting_url = meeting_url
        self.bot_name = bot_name
        self._playwright = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._meeting_ended = asyncio.Event()
        self._step = 0

    @property
    def page(self) -> Page | None:
        return self._page

    async def _screenshot(self, name: str):
        if self._page is None or self._page.is_closed():
            return
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
        self._step += 1
        ts = int(time.time())
        path = os.path.join(SCREENSHOTS_DIR, f"{self._step:02d}_{ts}_{name}.png")
        try:
            await self._page.screenshot(path=path, full_page=True)
            logger.info("Screenshot: %s", path)
        except Exception as e:
            logger.warning("Screenshot '%s' failed: %s", name, e)

    async def _dump_html(self, name: str):
        if self._page is None or self._page.is_closed():
            return
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
        path = os.path.join(SCREENSHOTS_DIR, f"{name}.html")
        try:
            html = await self._page.content()
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            logger.info("HTML dump: %s", path)
        except Exception as e:
            logger.warning("HTML dump '%s' failed: %s", name, e)

    async def join(self):
        """Войти в Телемост как гость."""
        self._playwright = await async_playwright().start()

        chrome_path = "/usr/bin/google-chrome-stable"
        if not os.path.exists(chrome_path):
            chrome_path = None

        user_id = os.getuid()
        launch_env = {
            **os.environ,
            "DISPLAY": os.getenv("DISPLAY", ":99"),
            "XDG_RUNTIME_DIR": os.getenv("XDG_RUNTIME_DIR", f"/run/user/{user_id}"),
        }

        self._browser = await self._playwright.chromium.launch(
            headless=False,
            executable_path=chrome_path,
            args=CHROME_ARGS,
            ignore_default_args=["--mute-audio"],
            env=launch_env,
        )

        context = await self._browser.new_context(
            permissions=["camera", "microphone"],
            viewport={"width": 1280, "height": 720},
            ignore_https_errors=True,
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        )

        self._page = await context.new_page()
        self._page.on("close", lambda _: self._meeting_ended.set())
        self._page.on("console", lambda msg: logger.debug("BROWSER: %s", msg.text))

        # Stealth — как в hh-recruiter-bot
        await self._page.evaluate_on_new_document("""
            () => {
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
                Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU', 'ru', 'en'] });
                window.chrome = { runtime: {} };
            }
        """)

        logger.info("Navigating to %s", self.meeting_url)
        await self._page.goto(self.meeting_url, wait_until="domcontentloaded", timeout=60000)
        await self._screenshot("after_navigate")
        await asyncio.sleep(5)

        # Проверяем капчу
        page_text = await self._page.evaluate("() => (document.body.innerText || '').substring(0, 500)")
        if any(w in page_text.lower() for w in ["captcha", "smartcaptcha", "robot"]):
            raise RuntimeError("Yandex SmartCaptcha detected — cannot proceed")
        logger.info("Page text: %s", page_text.replace("\n", " | ")[:200])

        # Ввод имени — через JS как в hh-recruiter-bot
        await self._enter_name()
        await self._screenshot("after_enter_name")

        # Отключить камеру на pre-join — через JS
        await self._mute_camera_prejoin()

        # Нажать "Подключиться" — через JS .click()
        await self._click_join()

        await asyncio.sleep(5)
        await self._screenshot("after_join_wait")

        # Мьютим микрофон после подключения
        await self._mute_mic_in_room()
        await asyncio.sleep(3)

        # Замьютить микрофон Chrome через PulseAudio
        await self._mute_chrome_mic()

        await self._dump_html("after_join")
        logger.info("Successfully joined meeting as '%s'", self.bot_name)

    async def _enter_name(self):
        """Ввести имя бота через JS — обходит React controlled inputs."""
        entered = await self._page.evaluate("""
            (name) => {
                const inputs = Array.from(document.querySelectorAll('input'));
                const inp = inputs.find(i =>
                    i.placeholder && (i.placeholder.includes('имя') || i.placeholder.includes('Имя'))
                ) || inputs.find(i => i.type === 'text');
                if (inp) {
                    const setter = Object.getOwnPropertyDescriptor(
                        HTMLInputElement.prototype, 'value'
                    ).set;
                    setter.call(inp, name);
                    inp.dispatchEvent(new Event('input', { bubbles: true }));
                    inp.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
                return false;
            }
        """, self.bot_name)
        if entered:
            logger.info("Entered bot name via JS")
        else:
            logger.warning("Could not find name input")

    async def _mute_camera_prejoin(self):
        """Отключить камеру на pre-join экране через JS."""
        await self._page.evaluate("""
            () => {
                const btns = Array.from(document.querySelectorAll('button'));
                btns.forEach(b => {
                    const label = (b.getAttribute('aria-label') || '').toLowerCase();
                    if (label.includes('камер')) b.click();
                });
            }
        """)
        logger.info("Muted camera (pre-join) via JS")

    async def _click_join(self):
        """Нажать кнопку подключения через JS .click() — обходит overlay."""
        joined = await self._page.evaluate("""
            () => {
                const btns = Array.from(document.querySelectorAll('button'));
                const btn = btns.find(b =>
                    b.textContent.includes('Подключиться') ||
                    b.textContent.includes('Присоединиться') ||
                    b.textContent.includes('Войти') ||
                    b.textContent.includes('Join')
                );
                if (btn) { btn.click(); return true; }
                return false;
            }
        """)
        logger.info("Join button clicked via JS: %s", joined)
        if not joined:
            await self._screenshot("join_button_not_found")
            raise RuntimeError("Could not find join button")

    async def _mute_mic_in_room(self):
        """Замьютить микрофон внутри комнаты через JS."""
        await self._page.evaluate("""
            () => {
                const btns = Array.from(document.querySelectorAll('button'));
                btns.forEach(b => {
                    const label = (b.getAttribute('aria-label') || '').toLowerCase();
                    if (label.includes('микрофон') || label.includes('microphone')) b.click();
                });
            }
        """)
        logger.info("Muted microphone (in-room) via JS")

    async def _mute_chrome_mic(self):
        """Замьютить микрофон Chrome через PulseAudio."""
        import subprocess
        try:
            result = subprocess.run(
                ["pactl", "list", "source-outputs", "short"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    idx = line.split()[0]
                    subprocess.run(
                        ["pactl", "set-source-output-mute", idx, "1"],
                        check=False, timeout=5,
                    )
            logger.info("Muted Chrome mic via PulseAudio")
        except Exception as e:
            logger.warning("Failed to mute Chrome mic: %s", e)

    async def wait_for_end(self):
        """Ждать завершения встречи."""
        check_count = 0
        while True:
            try:
                await asyncio.wait_for(self._meeting_ended.wait(), timeout=5)
                logger.info("Meeting ended (event set)")
                break
            except asyncio.TimeoutError:
                pass

            if self._page is None or self._page.is_closed():
                logger.info("Page closed, meeting ended")
                break

            try:
                ended = await self._page.evaluate("""
                    () => {
                        const text = document.body ? document.body.innerText : '';
                        if (text.includes('Встреча завершена') ||
                            text.includes('Meeting ended') ||
                            text.includes('Вы покинули') ||
                            text.includes('Конференция завершена'))
                            return 'ended';
                        return 'active';
                    }
                """)
                if ended == "ended":
                    logger.info("Meeting ended (detected end screen)")
                    await self._screenshot("meeting_ended")
                    break
            except Exception:
                # Page destroyed — meeting ended
                logger.info("Page gone, meeting ended")
                break

            # Проверяем redirect
            try:
                url = self._page.url
                if url and "/j/" not in url and "telemost" in url:
                    logger.warning("Redirected away from meeting: %s", url)
                    break
            except Exception:
                break

            check_count += 1
            if check_count % 12 == 0:
                await self._screenshot(f"heartbeat_{check_count // 12}m")

            await asyncio.sleep(5)

    async def leave(self):
        """Покинуть встречу и закрыть браузер."""
        logger.info("Leaving meeting...")

        if self._page and not self._page.is_closed():
            try:
                await self._page.evaluate("""
                    () => {
                        const btns = Array.from(document.querySelectorAll('button'));
                        const btn = btns.find(b =>
                            b.textContent.includes('Покинуть') ||
                            b.textContent.includes('Завершить')
                        );
                        if (btn) btn.click();
                    }
                """)
                await asyncio.sleep(1)
            except Exception:
                pass

            try:
                await self._page.close()
            except Exception:
                pass

        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass

        self._meeting_ended.set()
        logger.info("Left meeting and closed browser")
