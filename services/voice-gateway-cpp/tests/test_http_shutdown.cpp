// Hold pthread_create's return at the exact ownership boundary, without any
// test hooks in the gateway. The child exists, but std::thread has not yet
// been assigned to HandlerSlot. A concurrent stop must wait for publication.
#include "voice_gateway/http_server.h"

#include <arpa/inet.h>
#include <dlfcn.h>
#include <pthread.h>
#include <sys/socket.h>
#include <unistd.h>

#include <atomic>
#include <chrono>
#include <cstdlib>
#include <future>
#include <iostream>
#include <string>
#include <thread>

namespace {
thread_local bool delay_handler_publication = false;
std::atomic<bool> handler_created{false};
std::atomic<bool> release_handler{false};
}

extern "C" int pthread_create(pthread_t* thread, const pthread_attr_t* attributes,
                              void* (*entry)(void*), void* argument) noexcept {
    using Create = int (*)(pthread_t*, const pthread_attr_t*, void* (*)(void*), void*);
    static const auto create = reinterpret_cast<Create>(dlsym(RTLD_NEXT, "pthread_create"));
    if (!create) std::_Exit(2);
    const int result = create(thread, attributes, entry, argument);
    if (result == 0 && delay_handler_publication) {
        handler_created.store(true);
        while (!release_handler.load()) std::this_thread::yield();
    }
    return result;
}

int main() {
    voice_gateway::SessionRegistry registry;
    voice_gateway::HttpServer server("127.0.0.1", 18119, registry);
    std::string error;
    if (!server.start(error)) {
        std::cerr << "server start failed: " << error << std::endl;
        return 1;
    }
    const int fd = socket(AF_INET, SOCK_STREAM, 0);
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(18119);
    inet_pton(AF_INET, "127.0.0.1", &address.sin_addr);
    if (fd < 0 || connect(fd, reinterpret_cast<const sockaddr*>(&address), sizeof(address)) != 0) {
        if (fd >= 0) close(fd);
        std::cerr << "client connect failed" << std::endl;
        return 1;
    }
    shutdown(fd, SHUT_WR);
    std::thread serving([&server] {
        delay_handler_publication = true;
        server.run();
    });
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
    while (!handler_created.load() && std::chrono::steady_clock::now() < deadline) {
        std::this_thread::yield();
    }
    if (!handler_created.load()) {
        std::cerr << "FAIL: thread-creation boundary not reached" << std::endl;
        std::_Exit(1);
    }
    std::promise<void> stop_entered;
    auto entered = stop_entered.get_future();
    auto stopped = std::async(std::launch::async, [&] {
        stop_entered.set_value();
        server.stop();
    });
    entered.wait();
    if (stopped.wait_for(std::chrono::milliseconds(200)) == std::future_status::ready) {
        std::cerr << "FAIL: stop returned before handler ownership was published" << std::endl;
        // Old code has freed the slot. Do not resume a deliberate use-after-free.
        std::_Exit(1);
    }
    release_handler.store(true);
    if (stopped.wait_for(std::chrono::seconds(2)) != std::future_status::ready) {
        std::cerr << "FAIL: stop did not finish after publication" << std::endl;
        std::_Exit(1);
    }
    stopped.get();
    serving.join();
    close(fd);
    std::cout << "PASS: shutdown waits for handler publication and joins it" << std::endl;
    return 0;
}
