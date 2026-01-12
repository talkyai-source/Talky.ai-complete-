"use client";

import React, { useState, useRef, useEffect } from "react";
import { MessageSquare, X, Send, Loader2, RefreshCw, StopCircle } from "lucide-react";
import { useAuth } from "@/lib/auth-context";

interface Message {
    id: string;
    role: "user" | "assistant";
    content: string;
    timestamp: Date;
}

export function FloatingAssistant() {
    const [isOpen, setIsOpen] = useState(false);
    const [messages, setMessages] = useState<Message[]>([]);
    const [inputValue, setInputValue] = useState("");
    const [isLoading, setIsLoading] = useState(false);
    const [isConnected, setIsConnected] = useState(false);
    const [connectionError, setConnectionError] = useState<string | null>(null);
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const inputRef = useRef<HTMLInputElement>(null);
    const hasShownWelcomeRef = useRef(false);
    const { user } = useAuth();

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    };

    useEffect(() => {
        scrollToBottom();
    }, [messages]);

    // WebSocket connection effect - runs ONLY when isOpen changes
    // Following official React pattern: https://stackoverflow.com/a/60161181
    useEffect(() => {
        // Only connect when panel is open AND we have a user
        if (!isOpen || !user) {
            return;
        }

        // Don't reconnect if already connected or connecting
        if (wsRef.current?.readyState === WebSocket.OPEN ||
            wsRef.current?.readyState === WebSocket.CONNECTING) {
            return;
        }

        const token = localStorage.getItem("token");
        if (!token) {
            setConnectionError("Not authenticated. Please log in again.");
            return;
        }

        // Connect to assistant chat endpoint
        const wsUrl = `${process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000"}/api/v1/assistant/chat?token=${token}`;

        setConnectionError(null);
        const ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            setIsConnected(true);
            setConnectionError(null);
            // Add welcome message only on first connect
            if (!hasShownWelcomeRef.current) {
                hasShownWelcomeRef.current = true;
                setMessages([{
                    id: "welcome",
                    role: "assistant",
                    content: "Hello! I'm your AI assistant. I can help you with:\n\n- Checking dashboard stats\n- Managing leads and campaigns\n- Sending emails (with templates: meeting confirmation, follow-up, reminder)\n- Sending SMS messages\n- Booking meetings\n- And more!\n\nHow can I help you today?",
                    timestamp: new Date()
                }]);
            }
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);

                if (data.type === "message" || data.type === "assistant_message") {
                    setMessages(prev => [...prev, {
                        id: Date.now().toString(),
                        role: "assistant",
                        content: data.content || data.message,
                        timestamp: new Date()
                    }]);
                    setIsLoading(false);
                } else if (data.type === "assistant_typing") {
                    setIsLoading(data.content === true);
                } else if (data.type === "connected") {
                    console.log("Assistant connected:", data.message);
                } else if (data.type === "error") {
                    setMessages(prev => [...prev, {
                        id: Date.now().toString(),
                        role: "assistant",
                        content: `Sorry, I encountered an error. Please try again.`,
                        timestamp: new Date()
                    }]);
                    setIsLoading(false);
                }
            } catch (parseError) {
                console.error("Failed to parse message:", parseError);
            }
        };

        ws.onclose = (event) => {
            setIsConnected(false);
            wsRef.current = null;

            // Only show error if closed unexpectedly (not code 1000 = normal close)
            if (event.code !== 1000) {
                setConnectionError("Connection lost. Click retry to reconnect.");
            }
        };

        ws.onerror = () => {
            // WebSocket error events don't contain useful info, just log generic message
            console.error("WebSocket connection failed");
            setConnectionError("Connection error. Please try again.");
            setIsConnected(false);
        };

        wsRef.current = ws;

        // Cleanup function - close WebSocket when panel closes or component unmounts
        // Store ws in local variable to ensure we close the correct instance
        const wsCurrent = ws;
        return () => {
            if (wsCurrent.readyState === WebSocket.OPEN ||
                wsCurrent.readyState === WebSocket.CONNECTING) {
                wsCurrent.close(1000, "User closed panel");
            }
            wsRef.current = null;
        };
    }, [isOpen, user]); // Only depend on isOpen and user - NOT on any state that changes during connection

    // Focus input when panel opens
    useEffect(() => {
        if (isOpen && inputRef.current) {
            inputRef.current.focus();
        }
    }, [isOpen]);

    const sendMessage = () => {
        if (!inputValue.trim() || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
            return;
        }

        const userMessage: Message = {
            id: Date.now().toString(),
            role: "user",
            content: inputValue.trim(),
            timestamp: new Date()
        };

        setMessages(prev => [...prev, userMessage]);
        const messageToSend = inputValue.trim();
        setInputValue("");
        setIsLoading(true);

        // Send message to backend
        wsRef.current.send(JSON.stringify({
            type: "user_message",
            content: messageToSend
        }));
    };

    const stopLoading = () => {
        setIsLoading(false);
    };

    const handleReconnect = () => {
        // Close existing connection if any
        if (wsRef.current) {
            wsRef.current.close(1000, "Manual reconnect");
            wsRef.current = null;
        }
        setConnectionError(null);
        setIsConnected(false);

        // Toggle isOpen to trigger the useEffect to reconnect
        setIsOpen(false);
        setTimeout(() => setIsOpen(true), 100);
    };

    const handleKeyPress = (e: React.KeyboardEvent) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    };

    const toggleOpen = () => {
        setIsOpen(!isOpen);
    };

    if (!user) return null;

    return (
        <>
            {/* Floating Button */}
            {!isOpen && (
                <button
                    onClick={toggleOpen}
                    className="fixed bottom-6 right-6 z-50 w-14 h-14 rounded-full bg-gradient-to-r from-indigo-500 to-purple-600 text-white shadow-lg hover:shadow-xl hover:scale-105 transition-all duration-300 flex items-center justify-center group"
                    aria-label="Open AI Assistant"
                >
                    <MessageSquare className="w-6 h-6" />
                    <span className="absolute -top-10 right-0 bg-gray-800 text-white text-xs px-3 py-1.5 rounded-lg opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap">
                        AI Assistant
                    </span>
                </button>
            )}

            {/* Side Panel */}
            <div
                className={`fixed top-0 right-0 h-full bg-gray-900 border-l border-white/10 shadow-2xl z-50 transition-all duration-300 ease-in-out ${isOpen ? "w-[30%] min-w-[360px]" : "w-0"
                    } overflow-hidden`}
            >
                {isOpen && (
                    <div className="flex flex-col h-full">
                        {/* Header */}
                        <div className="flex items-center justify-between px-4 py-3 border-b border-white/10 bg-gray-800/50">
                            <div className="flex items-center gap-3">
                                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center">
                                    <MessageSquare className="w-4 h-4 text-white" />
                                </div>
                                <div>
                                    <h3 className="text-sm font-semibold text-white">AI Assistant</h3>
                                    <p className={`text-xs ${isConnected ? "text-green-400" : connectionError ? "text-red-400" : "text-yellow-400"}`}>
                                        {isConnected ? "Connected" : connectionError ? "Disconnected" : "Connecting..."}
                                    </p>
                                </div>
                            </div>
                            <div className="flex items-center gap-2">
                                {!isConnected && (
                                    <button
                                        onClick={handleReconnect}
                                        className="p-2 rounded-lg hover:bg-white/10 transition-colors"
                                        aria-label="Reconnect"
                                        title="Reconnect"
                                    >
                                        <RefreshCw className="w-4 h-4 text-gray-400" />
                                    </button>
                                )}
                                <button
                                    onClick={toggleOpen}
                                    className="p-2 rounded-lg hover:bg-white/10 transition-colors"
                                    aria-label="Close Assistant"
                                >
                                    <X className="w-5 h-5 text-gray-400" />
                                </button>
                            </div>
                        </div>

                        {/* Connection Error Banner */}
                        {connectionError && (
                            <div className="px-4 py-2 bg-red-900/30 border-b border-red-500/20 text-red-300 text-xs flex items-center justify-between">
                                <span>{connectionError}</span>
                                <button
                                    onClick={handleReconnect}
                                    className="text-red-200 hover:text-white underline"
                                >
                                    Retry
                                </button>
                            </div>
                        )}

                        {/* Messages */}
                        <div className="flex-1 overflow-y-auto p-4 space-y-4">
                            {messages.map((message) => (
                                <div
                                    key={message.id}
                                    className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}
                                >
                                    <div
                                        className={`max-w-[85%] rounded-2xl px-4 py-2.5 ${message.role === "user"
                                            ? "bg-gradient-to-r from-indigo-500 to-purple-600 text-white"
                                            : "bg-gray-800 text-gray-200 border border-white/5"
                                            }`}
                                    >
                                        <p className="text-sm whitespace-pre-wrap">{message.content}</p>
                                        <p className={`text-xs mt-1 ${message.role === "user" ? "text-white/60" : "text-gray-500"
                                            }`}>
                                            {message.timestamp.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                                        </p>
                                    </div>
                                </div>
                            ))}

                            {isLoading && (
                                <div className="flex justify-start">
                                    <div className="bg-gray-800 rounded-2xl px-4 py-3 border border-white/5">
                                        <div className="flex items-center gap-3">
                                            <Loader2 className="w-4 h-4 text-indigo-400 animate-spin" />
                                            <span className="text-sm text-gray-400">Thinking...</span>
                                            <button
                                                onClick={stopLoading}
                                                className="p-1 rounded hover:bg-white/10 transition-colors"
                                                title="Stop"
                                            >
                                                <StopCircle className="w-4 h-4 text-red-400" />
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            )}

                            <div ref={messagesEndRef} />
                        </div>

                        {/* Input */}
                        <div className="p-4 border-t border-white/10 bg-gray-800/30">
                            <div className="flex items-center gap-2">
                                <input
                                    ref={inputRef}
                                    type="text"
                                    value={inputValue}
                                    onChange={(e) => setInputValue(e.target.value)}
                                    onKeyPress={handleKeyPress}
                                    placeholder={isConnected ? "Type a message..." : "Connecting..."}
                                    className="flex-1 bg-gray-800 border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500/50 transition-all disabled:opacity-50"
                                    disabled={!isConnected || isLoading}
                                />
                                <button
                                    onClick={sendMessage}
                                    disabled={!inputValue.trim() || !isConnected || isLoading}
                                    className="p-2.5 rounded-xl bg-gradient-to-r from-indigo-500 to-purple-600 text-white disabled:opacity-50 disabled:cursor-not-allowed hover:opacity-90 transition-opacity"
                                    aria-label="Send message"
                                >
                                    <Send className="w-4 h-4" />
                                </button>
                            </div>
                            <p className="text-xs text-gray-500 mt-2 text-center">
                                Press Enter to send
                            </p>
                        </div>
                    </div>
                )}
            </div>

            {/* Overlay when open */}
            {isOpen && (
                <div
                    className="fixed inset-0 bg-black/20 z-40"
                    onClick={toggleOpen}
                />
            )}
        </>
    );
}
