from PIL import Image

from layoutcli.model import Rect, ViewNode
from layoutcli.screenshot import render_screenshot


def cell_colors(text, width, row, col):
    style = text.spans[row * width + col].style
    return style.color.triplet, style.bgcolor.triplet


def test_half_blocks_carry_top_and_bottom_pixels():
    img = Image.new("RGB", (4, 4), (255, 0, 0))
    for x in range(4):
        for y in (2, 3):
            img.putpixel((x, y), (0, 0, 255))
    text = render_screenshot(img, (4, 4), 4, 2)
    assert text.plain == "▀▀▀▀\n▀▀▀▀"
    assert cell_colors(text, 4, 0, 0) == ((255, 0, 0), (255, 0, 0))
    assert cell_colors(text, 4, 1, 3) == ((0, 0, 255), (0, 0, 255))


def test_selected_view_is_outlined():
    img = Image.new("RGB", (10, 10), (255, 255, 255))
    text = render_screenshot(img, (10, 10), 10, 5, ViewNode("a.B", bounds=Rect(0, 0, 10, 10)))
    assert cell_colors(text, 10, 0, 5)[0] == (255, 215, 0)
    assert cell_colors(text, 10, 2, 5)[0] == (255, 255, 255)


def test_highlight_maps_screen_coordinates_onto_smaller_image():
    img = Image.new("RGB", (10, 10), (255, 255, 255))  # screenshot downscaled from a 100x100 screen
    text = render_screenshot(img, (100, 100), 10, 5, ViewNode("a.B", bounds=Rect(50, 0, 100, 100)))
    assert cell_colors(text, 10, 2, 5)[0] == (255, 215, 0)
    assert cell_colors(text, 10, 2, 2)[0] == (255, 255, 255)


def test_degenerate_sizes_give_empty_text():
    img = Image.new("RGB", (4, 4))
    assert render_screenshot(img, (4, 4), 0, 5).plain == ""
    assert render_screenshot(img, (4, 4), 5, 0).plain == ""
