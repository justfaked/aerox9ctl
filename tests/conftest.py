import pytest
import rivalcfg.usbhid


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Never touch the real mouse or the real config: rivalcfg's simulated device + temp dirs."""
    monkeypatch.setenv("RIVALCFG_DRY", "1")
    monkeypatch.setenv("RIVALCFG_PROFILE", "1038:1858")
    monkeypatch.setenv("RIVALCFG_DEBUG_NO_COMMAND_DELAY", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def written(monkeypatch):
    """Everything sent to the simulated mouse, as one bytes string per opened session."""
    sessions = []
    original_close = rivalcfg.usbhid.FakeDevice.close

    def close(self):
        if not self.bytes.closed:
            sessions.append(self.bytes.getvalue())
        original_close(self)

    monkeypatch.setattr(rivalcfg.usbhid.FakeDevice, "close", close)
    return sessions


def packets(*commands: str) -> bytes:
    """Expected wire bytes: each output report is prefixed by type 0x02 and report id 0x00."""
    return b"".join(bytes.fromhex("02 00 " + command) for command in commands)
