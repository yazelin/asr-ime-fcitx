/*
 * Native Fcitx5 addon that commits ASR text coming from a local FIFO.
 * Hotkeys are configurable via ~/.config/asr-ime-fcitx/hotkeys.conf.
 */

#include <fcitx-utils/event.h>
#include <fcitx-utils/key.h>
#include <fcitx-utils/log.h>
#include <fcitx/addonfactory.h>
#include <fcitx/addonmanager.h>
#include <fcitx/inputcontext.h>
#include <fcitx/inputmethodengine.h>
#include <fcitx/instance.h>

#include <algorithm>
#include <cerrno>
#include <cctype>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

namespace {

constexpr const char *kCmdFifo = "/tmp/fcitx-asr-ime-cmd.fifo";
constexpr const char *kCommitFifo = "/tmp/fcitx-asr-ime-commit.fifo";

std::string trim(std::string s) {
    auto notSpace = [](unsigned char c) { return !std::isspace(c); };
    s.erase(s.begin(), std::find_if(s.begin(), s.end(), notSpace));
    s.erase(std::find_if(s.rbegin(), s.rend(), notSpace).base(), s.end());
    return s;
}

std::string hotkeyConfigPath() {
    if (const char *xdg = std::getenv("XDG_CONFIG_HOME"); xdg && *xdg) {
        return std::string(xdg) + "/asr-ime-fcitx/hotkeys.conf";
    }
    if (const char *home = std::getenv("HOME"); home && *home) {
        return std::string(home) + "/.config/asr-ime-fcitx/hotkeys.conf";
    }
    return {};
}

std::vector<fcitx::Key> defaultHotkeys() {
    return {
        fcitx::Key("Control+Alt+v"),
        fcitx::Key("Control+Alt+r"),
        fcitx::Key("F8"),
        fcitx::Key("Shift+F8"),
    };
}

std::vector<fcitx::Key> loadHotkeys() {
    auto path = hotkeyConfigPath();
    auto defaults = defaultHotkeys();
    if (path.empty()) {
        return defaults;
    }

    std::ifstream in(path);
    if (!in.is_open()) {
        return defaults;
    }

    std::vector<fcitx::Key> keys;
    std::string line;
    while (std::getline(in, line)) {
        line = trim(line);
        if (line.empty() || line[0] == '#') {
            continue;
        }
        fcitx::Key key(line);
        if (key.isValid()) {
            keys.push_back(key);
        }
    }
    if (!keys.empty()) {
        return keys;
    }
    return defaults;
}

bool ensureFifo(const char *path) {
    struct stat st {};
    if (::stat(path, &st) == 0) {
        if (S_ISFIFO(st.st_mode)) {
            return true;
        }
        ::unlink(path);
    }
    if (::mkfifo(path, 0600) == 0 || errno == EEXIST) {
        return true;
    }
    return false;
}

class ASRNativeEngine final : public fcitx::InputMethodEngineV2 {
public:
    explicit ASRNativeEngine(fcitx::Instance *instance) : instance_(instance) {
        if (!ensureFifo(kCmdFifo) || !ensureFifo(kCommitFifo)) {
            throw std::runtime_error("Failed to create ASR FIFO");
        }
        toggleKeys_ = loadHotkeys();

        commitFd_ = ::open(kCommitFifo, O_RDWR | O_NONBLOCK);
        if (commitFd_ < 0) {
            throw std::runtime_error("Failed to open commit FIFO");
        }

        fcitx::IOEventFlags ioFlags = fcitx::IOEventFlag::In;
        ioFlags |= fcitx::IOEventFlag::Err;
        ioFlags |= fcitx::IOEventFlag::Hup;

        commitEvent_ = instance_->eventLoop().addIOEvent(
            commitFd_, ioFlags, [this](fcitx::EventSourceIO *, int fd, fcitx::IOEventFlags) {
                return this->onCommitReadable(fd);
            });

        FCITX_INFO() << "ASR Native engine loaded, hotkeys: "
                     << fcitx::Key::keyListToString(toggleKeys_);
    }

    ~ASRNativeEngine() override {
        if (commitFd_ >= 0) {
            ::close(commitFd_);
        }
    }

    void activate(const fcitx::InputMethodEntry &entry,
                  fcitx::InputContextEvent &event) override {
        FCITX_UNUSED(entry);
        std::lock_guard<std::mutex> lock(mutex_);
        activeIC_ = event.inputContext();
    }

    void deactivate(const fcitx::InputMethodEntry &entry,
                    fcitx::InputContextEvent &event) override {
        FCITX_UNUSED(entry);
        std::lock_guard<std::mutex> lock(mutex_);
        if (activeIC_ == event.inputContext()) {
            activeIC_ = nullptr;
        }
    }

    void reset(const fcitx::InputMethodEntry &, fcitx::InputContextEvent &) override {}

    void keyEvent(const fcitx::InputMethodEntry &entry,
                  fcitx::KeyEvent &keyEvent) override {
        FCITX_UNUSED(entry);
        if (keyEvent.isRelease()) {
            return;
        }

        {
            std::lock_guard<std::mutex> lock(mutex_);
            activeIC_ = keyEvent.inputContext();
        }

        // Shift+F8 → command mode (voice command on selected text)
        if (keyEvent.key().normalize().check(fcitx::Key("Shift+F8"))) {
            sendCommand("command\n");
            keyEvent.filterAndAccept();
            return;
        }

        if (keyEvent.key().normalize().checkKeyList(toggleKeys_)) {
            sendCommand("toggle\n");
            keyEvent.filterAndAccept();
        }
    }

private:
    bool onCommitReadable(int fd) {
        char buf[4096];
        while (true) {
            ssize_t n = ::read(fd, buf, sizeof(buf));
            if (n > 0) {
                pending_.append(buf, static_cast<size_t>(n));
                continue;
            }
            if (n == 0) {
                break;
            }
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                break;
            }
            FCITX_WARN() << "Read commit FIFO failed: " << std::strerror(errno);
            break;
        }

        size_t pos = 0;
        while ((pos = pending_.find('\n')) != std::string::npos) {
            std::string line = pending_.substr(0, pos);
            pending_.erase(0, pos + 1);
            if (!line.empty() && line.back() == '\r') {
                line.pop_back();
            }
            commitLine(line);
        }
        return true;
    }

    void commitLine(const std::string &text) {
        if (text.empty()) {
            return;
        }
        fcitx::InputContext *ic = nullptr;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            ic = activeIC_;
        }
        if (!ic) {
            return;
        }
        ic->commitString(text);
    }

    void sendCommand(const char *cmd) {
        int fd = ::open(kCmdFifo, O_WRONLY | O_NONBLOCK);
        if (fd < 0) {
            FCITX_WARN() << "ASR daemon command FIFO not ready";
            return;
        }
        ssize_t len = static_cast<ssize_t>(std::strlen(cmd));
        ssize_t written = ::write(fd, cmd, static_cast<size_t>(len));
        if (written < 0) {
            FCITX_WARN() << "Write command failed: " << std::strerror(errno);
        }
        ::close(fd);
    }

    fcitx::Instance *instance_;
    std::mutex mutex_;
    fcitx::InputContext *activeIC_ = nullptr;
    std::unique_ptr<fcitx::EventSourceIO> commitEvent_;
    int commitFd_ = -1;
    std::string pending_;
    std::vector<fcitx::Key> toggleKeys_;
};

class ASRNativeEngineFactory : public fcitx::AddonFactory {
    fcitx::AddonInstance *create(fcitx::AddonManager *manager) override {
        return new ASRNativeEngine(manager->instance());
    }
};

} // namespace

FCITX_ADDON_FACTORY(ASRNativeEngineFactory);
