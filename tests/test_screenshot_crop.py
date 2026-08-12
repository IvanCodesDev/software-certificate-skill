from __future__ import annotations

import sys
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ingest_user_screenshots import detect_browser_chrome  # noqa: E402


def mac_browser_screenshot() -> tuple[Image.Image, int]:
    """Synthetic macOS-style browser window on a flat backdrop."""
    image = Image.new("RGB", (1620, 1000), (230, 230, 230))
    draw = ImageDraw.Draw(image)
    win = (60, 60, 1560, 940)
    chrome_bottom = 60 + 188
    draw.rectangle(win, fill="white", outline=(120, 120, 120), width=3)
    draw.rectangle((60, 60, 1560, chrome_bottom), fill=(213, 226, 251))
    for offset, color in ((0, (237, 106, 94)), (40, (245, 191, 79)), (80, (98, 197, 84))):
        x = 100 + offset
        draw.ellipse((x - 12, 90 - 12, x + 12, 90 + 12), fill=color)
    draw.rounded_rectangle((90, 160, 1500, 220), radius=28, fill=(243, 247, 254))
    for row in range(5):
        draw.rectangle((140, 320 + row * 120, 1480, 390 + row * 120), fill=(246, 248, 250))
    return image, chrome_bottom


class BrowserChromeCropTests(unittest.TestCase):
    def test_mac_frame_is_cropped_below_the_toolbar(self):
        image, chrome_bottom = mac_browser_screenshot()
        detected = detect_browser_chrome(image)
        self.assertIsNotNone(detected, "mac-style browser frame must be detected")
        box, meta = detected
        self.assertEqual(meta["style"], "mac_frame")
        self.assertGreater(box[1], chrome_bottom - 30, "crop must start near the chrome/content boundary")
        self.assertLess(box[1], chrome_bottom + 60, "crop must not eat into the page body")
        self.assertGreater(box[2] - box[0], image.width * 0.8)

    def test_desktop_application_screenshot_is_untouched(self):
        image = Image.new("RGB", (1440, 900), (240, 240, 240))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 1439, 72), fill=(34, 34, 34))
        for row in range(8):
            for col in range(10):
                shade = 70 + (row * 17 + col * 11) % 150
                draw.rectangle((40 + col * 135, 110 + row * 90, 150 + col * 135, 175 + row * 90),
                               fill=(shade, shade, shade))
        self.assertIsNone(detect_browser_chrome(image))

    def test_plain_page_screenshot_is_untouched(self):
        image = Image.new("RGB", (1600, 900), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 220, 899), fill=(238, 241, 246))
        draw.rectangle((260, 60, 1560, 120), fill=(58, 122, 254))
        for row in range(6):
            draw.rectangle((260, 160 + row * 110, 1560, 230 + row * 110), fill=(247, 248, 250))
        self.assertIsNone(detect_browser_chrome(image))


if __name__ == "__main__":
    unittest.main()
