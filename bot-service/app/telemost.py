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
    "--use-file-for-fake-audio-capture=/dev/null",
    "--autoplay-policy=no-user-gesture-required",
    "--disable-web-security",
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--window-size=1920,1080",
    "--disable-features=WebRtcHideLocalIpsWithMdns",
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

    async def _log_url(self, label: str):
        """Залогировать текущий URL страницы."""
        if self._page and not self._page.is_closed():
            url = self._page.url
            logger.info("[%s] Current URL: %s", label, url)
            return url
        return ""

    async def join(self):
        """Войти в Телемост как гость."""
        self._playwright = await async_playwright().start()

        chrome_path = "/usr/bin/google-chrome-stable"
        if not os.path.exists(chrome_path):
            chrome_path = None  # fallback на playwright chromium

        # Ensure PulseAudio env is set for Chrome audio output
        user_id = os.getuid()
        launch_env = {
            "DISPLAY": os.getenv("DISPLAY", ":99"),
            "XDG_RUNTIME_DIR": os.getenv("XDG_RUNTIME_DIR", f"/run/user/{user_id}"),
        }

        self._browser = await self._playwright.chromium.launch(
            headless=False,
            executable_path=chrome_path,
            args=CHROME_ARGS,
            ignore_default_args=["--mute-audio"],
            env={**os.environ, **launch_env},
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

        # Логируем console.log из браузера для отладки WebRTC
        self._page.on("console", lambda msg: logger.debug("BROWSER: %s", msg.text))

        logger.info("Navigating to %s", self.meeting_url)
        await self._page.goto(self.meeting_url, wait_until="domcontentloaded", timeout=60000)
        await self._log_url("after_navigate")
        await self._screenshot("after_navigate")

        # Ввод имени гостя
        await self._enter_name()
        await self._screenshot("after_enter_name")

        # Отключить камеру и микрофон ДО подключения (на pre-join экране)
        await self._mute_devices_prejoin()

        # Нажать "Подключиться"
        await self._click_join()

        # Проверить что мы действительно в комнате
        await self._verify_joined()

        # Выключить микрофон и камеру внутри комнаты
        await self._mute_devices_in_room()

        # Замьютить микрофон Chrome через PulseAudio (надёжный способ)
        await self._mute_chrome_mic()

        # Дамп для отладки
        await self._dump_html("after_join")

        logger.info("Successfully joined meeting as '%s'", self.bot_name)

    async def _enter_name(self):
        """Ввести имя бота в поле гостевого входа."""
        selectors = [
            'input[data-testid="guest-name-input"]',
            'input[placeholder*="имя"]',
            'input[placeholder*="Имя"]',
            'input[placeholder*="name"]',
            'input[type="text"]',
        ]
        for selector in selectors:
            try:
                input_el = await self._page.wait_for_selector(
                    selector, timeout=5000
                )
                if input_el:
                    # Очистить поле перед вводом
                    await input_el.click(click_count=3)
                    await input_el.fill(self.bot_name)
                    logger.info("Entered bot name using selector: %s", selector)
                    return
            except Exception:
                continue

        await self._screenshot("name_input_not_found")
        logger.warning("Could not find name input, proceeding without name entry")

    async def _mute_chrome_mic(self):
        """Замьютить микрофон Chrome через PulseAudio — убирает писк."""
        import subprocess
        try:
            # Получить все source-outputs (микрофоны приложений)
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
            logger.info("Muted Chrome microphone via PulseAudio")
        except Exception as e:
            logger.warning("Failed to mute Chrome mic via PulseAudio: %s", e)

    async def _mute_devices_in_room(self):
        """Выключить камеру и микрофон внутри комнаты встречи."""
        mute_selectors = [
            '[data-testid="mic-button"]',
            'button[aria-label*="микрофон"]',
            'button[aria-label*="Микрофон"]',
            'button[aria-label*="Microphone"]',
            '[data-testid="camera-button"]',
            'button[aria-label*="камер"]',
            'button[aria-label*="Камер"]',
            'button[aria-label*="Camera"]',
        ]
        for selector in mute_selectors:
            try:
                btn = await self._page.query_selector(selector)
                if btn:
                    # Проверяем, включено ли устройство (aria-pressed="true" или нет атрибута)
                    aria_pressed = await btn.get_attribute("aria-pressed")
                    is_muted = await btn.get_attribute("data-muted")
                    if aria_pressed != "false" and is_muted != "true":
                        await btn.click()
                        logger.info("Muted device (in-room): %s", selector)
                        await asyncio.sleep(0.5)
            except Exception:
                continue

        # НЕ отключаем аудиотреки через JS — это убивает входящий звук встречи!

    async def _mute_devices_prejoin(self):
        """Отключить камеру и микрофон на pre-join экране."""
        # На pre-join экране есть иконки камеры/микрофона — пробуем их нажать
        mute_selectors = [
            '[data-testid="mic-button"]',
            '[data-testid="camera-button"]',
            'button[aria-label*="микрофон"]',
            'button[aria-label*="камер"]',
            'button[aria-label*="Микрофон"]',
            'button[aria-label*="Камер"]',
            'button[aria-label*="Microphone"]',
            'button[aria-label*="Camera"]',
        ]
        for selector in mute_selectors:
            try:
                btn = await self._page.query_selector(selector)
                if btn:
                    is_active = await btn.get_attribute("aria-pressed")
                    if is_active != "false":
                        await btn.click()
                        logger.info("Muted device (pre-join): %s", selector)
            except Exception:
                continue

    async def _click_join(self):
        """Нажать кнопку подключения к встрече."""
        # Телемост использует "Подключиться" на pre-join экране
        selectors = [
            'button:has-text("Подключиться")',
            'button:has-text("Присоединиться")',
            'button:has-text("Продолжить")',
            'button:has-text("Войти")',
            '[data-testid="join-button"]',
        ]

        pre_click_url = await self._log_url("before_click_join")

        for selector in selectors:
            try:
                btn = await self._page.wait_for_selector(selector, timeout=5000)
                if btn:
                    # Проверяем текст кнопки
                    btn_text = await btn.text_content()
                    logger.info("Found button '%s' via selector: %s", btn_text, selector)

                    await btn.click()
                    logger.info("Clicked join button: %s", selector)

                    # Ждём навигацию или изменение страницы
                    await asyncio.sleep(5)
                    await self._log_url("after_click_join")
                    await self._screenshot("after_click_join")
                    return
            except Exception:
                continue

        # Fallback: если стандартные селекторы не сработали,
        # ищем любую зелёную кнопку с текстом (Телемост стилизует join-кнопку)
        logger.warning("Standard selectors failed, trying fallback...")
        try:
            # Ищем кнопку по тексту напрямую через JS
            btn = await self._page.evaluate_handle("""
                () => {
                    const buttons = document.querySelectorAll('button');
                    for (const b of buttons) {
                        const text = b.textContent.trim();
                        if (text === 'Подключиться' || text === 'Присоединиться') {
                            return b;
                        }
                    }
                    return null;
                }
            """)
            if btn:
                await btn.as_element().click()
                logger.info("Clicked join button via JS fallback")
                await asyncio.sleep(5)
                await self._log_url("after_click_join_fallback")
                await self._screenshot("after_click_join_fallback")
                return
        except Exception as e:
            logger.warning("JS fallback failed: %s", e)

        await self._screenshot("join_button_not_found")
        logger.error("Could not find join button!")

    async def _verify_joined(self):
        """Проверить что мы действительно вошли в комнату встречи."""
        await asyncio.sleep(3)
        url = await self._log_url("verify_joined")
        await self._screenshot("verify_joined")

        # Если URL содержит /j/ — мы всё ещё на странице встречи (хорошо)
        # Если URL — главная telemost.yandex.ru без /j/ — нас выкинуло
        if url and "/j/" not in url:
            logger.error(
                "JOIN FAILED! Redirected to %s — not in meeting room. "
                "Possible causes: WebRTC failure, meeting expired, or auth required.",
                url,
            )
            await self._dump_html("join_failed")
            raise RuntimeError(
                f"Failed to join meeting: redirected to {url}"
            )

        # Проверяем наличие элементов комнаты (видео, кнопки управления)
        room_indicators = [
            'button:has-text("Покинуть")',
            'button:has-text("Завершить")',
            '[data-testid="leave-button"]',
            'video',
        ]
        in_room = False
        for selector in room_indicators:
            try:
                el = await self._page.query_selector(selector)
                if el:
                    logger.info("Room indicator found: %s", selector)
                    in_room = True
                    break
            except Exception:
                continue

        if not in_room:
            logger.warning(
                "No room indicators found after join. URL: %s. "
                "Bot may not be in the meeting.",
                url,
            )

    async def wait_for_end(self):
        """Ждать завершения встречи."""
        check_count = 0
        while True:
            # Проверяем event (ставится из /leave endpoint)
            try:
                await asyncio.wait_for(self._meeting_ended.wait(), timeout=5)
                logger.info("Meeting ended (event set)")
                break
            except asyncio.TimeoutError:
                pass

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

            # Проверяем, не выкинуло ли нас на главную
            try:
                url = self._page.url
                if url and "/j/" not in url and "telemost" in url:
                    logger.warning("Detected redirect away from meeting: %s", url)
                    await self._screenshot("redirected_from_meeting")
                    break
            except Exception:
                pass

            # Периодический скриншот каждые 60 секунд
            check_count += 1
            if check_count % 12 == 0:
                await self._screenshot(f"heartbeat_{check_count // 12}m")

            await asyncio.sleep(5)

    async def leave(self):
        """Покинуть встречу и закрыть браузер."""
        logger.info("Leaving meeting...")

        if self._page and not self._page.is_closed():
            await self._screenshot("before_leave")

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
