"use client";

import type React from "react";

interface AskAICardProps {
    onClick?: () => void;
}

export const AskAICard: React.FC<AskAICardProps> = ({ onClick }) => {
    return (
        <div
            className="ask-ai-card"
            onClick={onClick}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => e.key === "Enter" && onClick?.()}
        >
            {/* Glowing Orb */}
            <div className="ask-ai-orb-container">
                <div className="ask-ai-orb">
                    <div className="ask-ai-orb-glow" />
                </div>
            </div>

            {/* Text */}
            <div className="ask-ai-text">
                <h3 className="ask-ai-title">Ask AI</h3>
                <p className="ask-ai-subtitle">Get answers</p>
            </div>

            <style jsx>{`
                .ask-ai-card {
                    position: relative;
                    width: 280px;
                    height: 320px;
                    background: linear-gradient(
                        180deg,
                        rgba(30, 41, 59, 0.8) 0%,
                        rgba(15, 23, 42, 0.95) 100%
                    );
                    border-radius: 24px;
                    border: 1px solid rgba(100, 116, 139, 0.3);
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    cursor: pointer;
                    transition: all 0.3s ease;
                    overflow: hidden;
                    backdrop-filter: blur(20px);
                    box-shadow: 
                        0 0 60px rgba(234, 179, 8, 0.15),
                        0 0 100px rgba(234, 88, 12, 0.1),
                        inset 0 1px 1px rgba(255, 255, 255, 0.1);
                }

                .ask-ai-card:hover {
                    transform: translateY(-4px);
                    border-color: rgba(100, 116, 139, 0.5);
                    box-shadow: 
                        0 0 80px rgba(234, 179, 8, 0.2),
                        0 0 120px rgba(234, 88, 12, 0.15),
                        inset 0 1px 1px rgba(255, 255, 255, 0.15);
                }

                .ask-ai-orb-container {
                    position: relative;
                    width: 180px;
                    height: 180px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    margin-bottom: 20px;
                }

                .ask-ai-orb {
                    position: relative;
                    width: 160px;
                    height: 160px;
                    border-radius: 50%;
                    background: linear-gradient(
                        135deg,
                        #ea580c 0%,
                        #eab308 30%,
                        #84cc16 60%,
                        #22c55e 100%
                    );
                    box-shadow: 
                        0 0 60px rgba(234, 88, 12, 0.5),
                        0 0 100px rgba(234, 179, 8, 0.3),
                        inset 0 0 30px rgba(0, 0, 0, 0.3);
                    animation: orbPulse 4s ease-in-out infinite;
                }

                .ask-ai-orb-glow {
                    position: absolute;
                    inset: -40px;
                    border-radius: 50%;
                    background: radial-gradient(
                        circle,
                        rgba(234, 179, 8, 0.4) 0%,
                        rgba(234, 88, 12, 0.2) 40%,
                        transparent 70%
                    );
                    animation: glowPulse 4s ease-in-out infinite;
                    z-index: -1;
                }

                @keyframes orbPulse {
                    0%, 100% {
                        transform: scale(1);
                    }
                    50% {
                        transform: scale(1.02);
                    }
                }

                @keyframes glowPulse {
                    0%, 100% {
                        opacity: 0.6;
                        transform: scale(1);
                    }
                    50% {
                        opacity: 1;
                        transform: scale(1.1);
                    }
                }

                .ask-ai-text {
                    text-align: center;
                    z-index: 1;
                }

                .ask-ai-title {
                    font-size: 1.5rem;
                    font-weight: 600;
                    color: #f1f5f9;
                    margin: 0;
                    letter-spacing: -0.02em;
                }

                .ask-ai-subtitle {
                    font-size: 0.875rem;
                    color: #94a3b8;
                    margin: 8px 0 0 0;
                    font-weight: 400;
                }
            `}</style>
        </div>
    );
};

export default AskAICard;
