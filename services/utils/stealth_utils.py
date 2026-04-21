import asyncio
import random
import math

async def human_delay(min_ms=500, max_ms=1500):
    """Realistic human pause."""
    await asyncio.sleep(random.uniform(min_ms, max_ms) / 1000.0)

async def human_move_mouse(page, x, y):
    """Moves mouse in a realistic non-linear path to target coordinates."""
    current_mouse = page.mouse
    # Get current position is tricky in Playwright, so we assume a start point if unknown
    # or just move relative to current.
    # For simplicity, we just do a multi-step move.
    steps = random.randint(10, 25)
    for i in range(steps):
        # Add some 'jitter' to the move
        await current_mouse.move(x + random.randint(-5, 5), y + random.randint(-5, 5))
        await asyncio.sleep(random.uniform(5, 20) / 1000.0)

async def human_click(page, selector, timeout=5000):
    """Moves mouse to element and clicks with human-like timing."""
    element = await page.wait_for_selector(selector, timeout=timeout)
    if not element:
        return False
    
    box = await element.bounding_box()
    if not box:
        return False

    # Target the center with a bit of randomness
    target_x = box['x'] + box['width'] / 2 + random.uniform(-5, 5)
    target_y = box['y'] + box['height'] / 2 + random.uniform(-5, 5)

    await page.mouse.move(target_x, target_y, steps=random.randint(15, 30))
    await asyncio.sleep(random.uniform(100, 400) / 1000.0) # Hover delay
    await page.mouse.click(target_x, target_y)
    return True

async def human_scroll(page, distance=None):
    """Scrolls down in realistic chunks."""
    if distance is None:
        distance = random.randint(300, 800)
    
    steps = random.randint(5, 12)
    chunk = distance / steps
    for _ in range(steps):
        await page.mouse.wheel(0, chunk + random.uniform(-20, 20))
        await asyncio.sleep(random.uniform(50, 200) / 1000.0)

async def human_type(page, selector, text):
    """Types text with variable delays between keystrokes."""
    await page.focus(selector)
    for char in text:
        await page.keyboard.type(char)
        await asyncio.sleep(random.uniform(50, 250) / 1000.0)
