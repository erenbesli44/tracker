import pytest

from src.jobs.broker_watch.config import load_broker_config


def test_load_real_config():
    config = load_broker_config()

    assert [b.slug for b in config.brokers] == ["is-yatirim", "yapi-kredi-yatirim", "gedik-yatirim"]
    assert [b.adapter for b in config.brokers] == ["isy_rss", "yk_json", "gedik_page"]
    assert all(b.enabled and b.url.startswith("https://") for b in config.brokers)
    assert config.brokers[0].site_url == "https://arastirma.isyatirim.com.tr"
    assert config.defaults.user_agent == "dovizveri-bot/0.1 (+https://dovizveri.com/iletisim)"
    assert config.defaults.max_runs_per_day == 2
    assert len(config.enabled_brokers) == 3


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_broker_config(str(tmp_path / "missing.yaml"))


@pytest.mark.parametrize(
    "body",
    [
        "- just a list\n",
        "brokers:\n  - slug: Bad Slug\n    name: X\n    adapter: isy_rss\n    url: https://x\n",
        "brokers:\n  - slug: x\n    name: X\n    adapter: unknown\n    url: https://x\n",
        "brokers:\n  - slug: x\n    name: X\n    adapter: isy_rss\n    url: ftp://x\n",
        (
            "brokers:\n"
            "  - {slug: x, name: X, adapter: isy_rss, url: https://x}\n"
            "  - {slug: x, name: Y, adapter: yk_json, url: https://y}\n"
        ),
    ],
)
def test_invalid_config_raises_value_error(tmp_path, body):
    path = tmp_path / "brokers.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError):
        load_broker_config(str(path))
