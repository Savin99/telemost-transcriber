import asyncio
import logging
import os
import time

from playwright.async_api import Browser, Page, async_playwright

logger = logging.getLogger(__name__)

SCREENSHOTS_DIR = os.getenv("SCREENSHOTS_DIR", "/workspace/screenshots")

# Chrome-флаги для WebRTC в контейнере
CHROME_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
    "--enable-audio-service-out-of-process",
    "--disable-web-security",
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--window-size=1920,1080",
]


class TelemostSession:
    """Управление сессией Телемоста через Playwright."""

    def __init__(self, meeting_url: str, bot_name: str):
        self.meeting_url = meeting_url
        self.bot_name = bot_name
        self._playwright = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._meeting_ended = asyncio.Event()
        self._step = 0

    async def _screenshot(self, name: str):
        """Сохранить скриншот текущего состояния страницы."""
        if self._page is None or self._page.is_closed():
            logger.warning("Cannot take screenshot '%s': page is closed", name)
            return
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
        self._step += 1
        ts = int(time.time())
        filename = f"{self._step:02d}_{ts}_{name}.png"
        path = os.path.join(SCREENSHOTS_DIR, filename)
        try:
            await self._page.screenshot(path=path, full_page=True)
            logger.info("Screenshot saved: %s", path)
        except Exception as e:
            logger.warning("Screenshot '%s' failed: %s", name, e)

    async def join(self):
        """Войти в Телемост как гость."""
        self._playwright = await async_playwright().start()

        chrome_path = "/usr/bin/google-chrome-stable"
        if not os.path.exists(chrome_path):
            chrome_path = None  # fallback на playwright chromium

        self._browser = await self._playwright.chromium.launch(
            headless=False,
            executable_path=chrome_path,
            args=CHROME_ARGS,
            ignore_default_args=["--mute-audio"],
        )

        context = await self._browser.new_context(
            permissions=["camera", "microphone"],
            viewport={"width": 1920, "height": 1080},
            ignore_https_errors=True,
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
            ),
        )
        await context.grant_permissions(
            ["microphone", "camera"], origin=self.meeting_url
        )

        self._page = await context.new_page()
        self._page.on("close", lambda _: self._meeting_ended.set())

        logger.info("Navigating to %s", self.meeting_url)
        await self._page.goto(self.meeting_url, wait_until="networkidle")
        await self._screenshot("after_navigate")

        # Ввод имени гостя
        await self._enter_name()
        await self._screenshot("after_enter_name")

        # Нажать "Присоединиться"
        await self._click_join()
        await self._screenshot("after_click_join")

        # Отключить камеру и микрофон
        await self._mute_devices()
        await self._screenshot("after_mute_devices")

        # Дополнительный скриншот через 5 секунд — видно ли комнату
        await asyncio.sleep(5)
        await self._screenshot("in_meeting_5s")

        # Дамп HTML для отладки
        await self._dump_html("after_join")

        logger.info("Successfully joined meeting as '%s'", self.bot_name)

    async def _dump_html(self, name: str):
        """Сохранить HTML страницы для отладки селекторов."""
        if self._page is None or self._page.is_closed():
            return
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
        path = os.path.join(SCREENSHOTS_DIR, f"{name}.html")
        try:
            html = await self._page.content()
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            logger.info("HTML dump saved: %s", path)
        except Exception as e:
            logger.warning("HTML dump '%s' failed: %s", name, e)

    async def _enter_name(self):
        """Ввести имя бота в поле гостевого входа."""
        # Телемост показывает поле для ввода имени гостя
        # Пробуем несколько вариантов селекторов
        selectors = [
            'input[data-testid="guest-name-input"]',
            'input[placeholder*="имя"]',
            'input[placeholder*="Имя"]',
            'input[type="text"]',
        ]
        for selector in selectors:
            try:
                input_el = await self._page.wait_for_selector(
                    selector, timeout=5000
                )
                if input_el:
                    await input_el.fill(self.bot_name)
                    logger.info("Entered bot name using selector: %s", selector)
                    return
            except Exception:
                continue

        await self._screenshot("name_input_not_found")
        logger.warning("Could not find name input, proceeding without name entry")

    async def _click_join(self):
        """Нажать кнопку присоединения."""
        selectors = [
            'button:has-text("Присоединиться")',
            'button:has-text("Продолжить")',
            'button:has-text("Войти")',
            '[data-testid="join-button"]',
            'button.Orb-Button',
        ]
        for selector in selectors:
            try:
                btn = await self._page.wait_for_selector(selector, timeout=5000)
                if btn:
                    await btn.click()
                    logger.info("Clicked join button: %s", selector)
                    # Ждём загрузки интерфейса встречи
                    await asyncio.sleep(3)
                    await self._screenshot("after_join_wait")
                    return
            except Exception:
                continue

        await self._screenshot("join_button_not_found")
        logger.warning("Could not find join button, may already be in meeting")

    async def _mute_devices(self):
        """Отключить камеру и микрофон бота."""
        # Попытка найти и нажать кнопки выключения камеры/микрофона
        mute_selectors = [
            '[data-testid="mic-button"]',
            '[data-testid="camera-button"]',
            'button[aria-label*="микрофон"]',
            'button[aria-label*="камер"]',
            'button[aria-label*="Microphone"]',
            'button[aria-label*="Camera"]',
        ]
        for selector in mute_selectors:
            try:
                btn = await self._page.query_selector(selector)
                if btn:
                    # Проверяем, не выключено ли уже
                    aria_pressed = await btn.get_attribute("aria-pressed")
                    if aria_pressed != "false":
                        await btn.click()
                        logger.info("Muted device: %s", selector)
            except Exception:
                continue

    async def wait_for_end(self):
        """Ждать завершения встречи."""
        check_count = 0
        while not self._meeting_ended.is_set():
            # Проверяем что страница жива
            if self._page is None or self._page.is_closed():
                logger.info("Page closed, meeting ended")
                break

            # Проверяем наличие признаков завершения встречи
            try:
                ended = await self._page.query_selector(
                    'text=/[Вв]стреча завершена|[Вв]ы покинули/'
                )
                if ended:
                    logger.info("Meeting ended (detected end screen)")
                    await self._screenshot("meeting_ended")
                    break
            except Exception:
                pass

            # Периодический скриншот каждые 60 секунд (12 * 5s)
            check_count += 1
            if check_count % 12 == 0:
                await self._screenshot(f"heartbeat_{check_count // 12}m")

            await asyncio.sleep(5)

    async def leave(self):
        """Покинуть встречу и закрыть браузер."""
        logger.info("Leaving meeting...")

        if self._page and not self._page.is_closed():
            await self._screenshot("before_leave")

            # Попробовать нажать "Покинуть"
            try:
                leave_btn = await self._page.query_selector(
                    'button:has-text("Покинуть"), '
                    'button:has-text("Завершить"), '
                    '[data-testid="leave-button"]'
                )
                if leave_btn:
                    await leave_btn.click()
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
