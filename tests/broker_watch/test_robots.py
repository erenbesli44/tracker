import pytest

from src.jobs.broker_watch.robots import RobotsRules

UA = "dovizveri-bot/0.1 (+https://dovizveri.com/iletisim)"
PDF = "https://cdn.gedik.com/media/x/a.pdf"
PUBLIC_PDF = "https://cdn.gedik.com/media/public/y.pdf"
PAGE = "https://cdn.gedik.com/analiz/model-portfoy"
YK_FILE = "https://www.ykyatirim.com.tr/DetailPage/GetFilePath?url=%2FFiles%2Fa.pdf"


@pytest.mark.parametrize(
    "robots, allowed",
    [
        ("User-agent: *\nDisallow: /*.pdf$\n", {PDF: False, PUBLIC_PDF: False, PAGE: True}),
        (
            "User-agent: *\nDisallow: /media/*.pdf$\n",
            {PDF: False, PAGE: True, YK_FILE: True},
        ),
        ("User-agent: *\nDisallow: /media/\n", {PDF: False, PUBLIC_PDF: False, PAGE: True}),
        (
            "User-agent: *\nDisallow: /media/\nAllow: /media/public/\n",
            {PDF: False, PUBLIC_PDF: True},  # longest matching rule wins
        ),
        (
            "User-agent: *\nDisallow:\n\nUser-agent: dovizveri-bot/0.1\nDisallow: /\n",
            {PDF: False, PAGE: False},  # our group (token before '/') beats '*'
        ),
        (
            "User-agent: *\nDisallow: /\n\nUser-agent: dovizveri-bot\nDisallow:\n",
            {PDF: True, PAGE: True},  # empty Disallow in our group allows everything
        ),
        ("User-agent: Googlebot\nDisallow: /\n", {PDF: True, PAGE: True}),  # no group for us
        ("", {PDF: True}),
        (
            "user-agent: DOVIZVERI-BOT # comment\ndisallow: /media # comment\n",
            {PDF: False, PAGE: True},
        ),
        (
            "User-agent: *\nDisallow: /*?paged=\n",
            {"https://x.com/feed/?paged=2": False, PAGE: True},
        ),
        ("User-agent: *\nAllow: /media/\nDisallow: /media/\n", {PDF: True}),  # Allow wins ties
    ],
)
def test_can_fetch(robots, allowed):
    rules = RobotsRules(robots, UA)
    assert {url: rules.can_fetch(url) for url in allowed} == allowed


def test_disallow_all_blocks_every_url():
    rules = RobotsRules.disallow_all()
    assert not rules.can_fetch(PAGE) and not rules.can_fetch("https://cdn.gedik.com/")
