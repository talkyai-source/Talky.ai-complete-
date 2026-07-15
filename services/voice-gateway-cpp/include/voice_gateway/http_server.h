#pragma once

#include <atomic>
#include <cstdint>
#include <list>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

#include "voice_gateway/session_registry.h"

namespace voice_gateway {

class HttpServer {
public:
    HttpServer(std::string host, uint16_t port, SessionRegistry& registry);
    ~HttpServer();

    HttpServer(const HttpServer&) = delete;
    HttpServer& operator=(const HttpServer&) = delete;

    bool start(std::string& error);
    void run();
    void stop();

    [[nodiscard]] bool healthy() const;

private:
    // One live HTTP request. The slot OWNS its joinable thread and its client
    // fd; the request's identity is this object, never the reusable integer fd
    // (review #1). All fd/done mutations happen under handlers_mutex_, so a
    // shutdown() from stop() can never race the handler's own close() onto a
    // recycled fd number. The thread is JOINED (by reap or stop) before the
    // slot is destroyed — join is the only true completion barrier (review #2);
    // an "fd set is empty" condition is not.
    struct HandlerSlot {
        std::thread thread;
        int fd{-1};
        bool closed{false};  // fd has been ::close()d (under handlers_mutex_)
        bool done{false};    // handler body finished (under handlers_mutex_)
    };

    void handle_client(int client_fd);
    // Thread body: run handle_client, then close+mark done under the lock.
    void handler_main(HandlerSlot* slot);
    // Admit and spawn an owned handler for an accepted fd. On any failure the
    // fd is closed and the admission count rolled back (exception-transactional,
    // review #9). Returns false if the request was not admitted.
    bool spawn_handler(int client_fd);
    // Join+destroy finished handler slots (their threads are already past all
    // shared state, so each join returns ~immediately). Called opportunistically
    // from the accept loop so long uptimes don't accumulate joinable corpses.
    void reap_finished_handlers();
    void close_listener();

    // Hard cap on concurrently-executing HTTP handlers. Each handler is a
    // thread; without a cap a connection flood spawns unbounded threads and
    // takes the process down (VG-04). Sized for control-plane + audio/TTS
    // callback traffic across all sessions with generous headroom.
    static constexpr int kMaxActiveHandlers = 256;

    std::string host_;
    uint16_t port_;
    SessionRegistry& registry_;

    std::atomic<bool> running_{false};
    std::atomic<bool> healthy_{true};

    // Handler ownership. Guarded by handlers_mutex_: the slot list, the active
    // count, draining_, and every slot's fd/closed/done fields. stop() sets
    // draining_, shutdown()s every still-open tracked fd to unblock its
    // handler, then splices the whole list out and JOINS every thread — no
    // timeout escape, no completion proxy (VG-03 / reviews #1 #2).
    std::mutex handlers_mutex_;
    std::list<std::unique_ptr<HandlerSlot>> handlers_;
    int active_handler_count_{0};
    bool draining_{false};

    // HttpServer is single-use: after stop() it can NOT be start()ed again (the
    // old listener is gone and draining_ is latched). Explicit, not implicit.
    bool stopped_{false};

    int server_fd_{-1};
};

}  // namespace voice_gateway
