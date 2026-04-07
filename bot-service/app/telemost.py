import asyncio
import logging
import os

from playwright.async_api import Browser, Page, async_playwright

logger = logging.getLogger(__name__)

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

        # Ввод имени гостя
        await self._enter_name()

        # Нажать "Присоединиться"
        await self._click_join()

        # Отключить камеру и микрофон
        await self._mute_devices()

        logger.info("Successfully joined meeting as '%s'", self.bot_name)

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
                    return
            except Exception:
                continue

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
                    break
            except Exception:
                pass

            await asyncio.sleep(5)

    async def leave(self):
        """Покинуть встречу и закрыть браузер."""
        logger.info("Leaving meeting...")

        if self._page and not self._page.is_closed():
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
