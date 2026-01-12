"use client";

import { Canvas, useFrame } from "@react-three/fiber";
import { Bloom, EffectComposer } from "@react-three/postprocessing";
import { KernelSize } from "postprocessing";
import type React from "react";
import { useMemo, useRef, useState, useCallback, useEffect } from "react";
import * as THREE from "three";
import BlurEffect from "react-progressive-blur";
import { MagneticText } from "./morphing-cursor";

type AIState = "idle" | "connecting" | "listening" | "processing" | "speaking";

interface HelixRingsProps {
    levelsUp?: number;
    levelsDown?: number;
    stepY?: number;
    rotationStep?: number;
    aiState?: AIState;
    audioLevel?: number;
}

const HelixRings: React.FC<HelixRingsProps> = ({
    levelsUp = 10,
    levelsDown = 10,
    stepY = 0.85,
    rotationStep = Math.PI / 16,
    aiState = "idle",
    audioLevel = 0,
}) => {
    const groupRef = useRef<THREE.Group>(new THREE.Group());
    const meshRefs = useRef<THREE.Mesh[]>([]);
    const timeRef = useRef(0);
    const transitionRef = useRef(0);

    const isActive = aiState !== "idle";

    useFrame((_, delta) => {
        if (groupRef.current) {
            timeRef.current += delta;
            const targetTransition = isActive ? 1 : 0;
            transitionRef.current += (targetTransition - transitionRef.current) * 0.15;
            const t = transitionRef.current;

            if (!isActive) {
                groupRef.current.rotation.y += 0.005;
            }

            groupRef.current.position.x = 5;
            groupRef.current.position.y = 0;
            groupRef.current.position.z = 0;

            const totalRings = levelsUp + levelsDown + 1;

            meshRefs.current.forEach((mesh, index) => {
                if (mesh) {
                    const helixY = (index - levelsDown) * stepY;
                    const helixRotY = (index - levelsDown) * rotationStep;
                    const waveSpacing = 0.5;
                    const centerIndex = totalRings / 2;
                    const distanceFromCenter = Math.abs(index - centerIndex);
                    const waveX = (index - centerIndex) * waveSpacing;
                    const baseHeight = 0.15;
                    const maxHeight = 2.5;
                    const falloff = 1 - (distanceFromCenter / (totalRings / 2)) * 0.85;
                    const audioReaction = audioLevel > 0.01 ? audioLevel * maxHeight * falloff : 0;
                    const finalHeight = baseHeight + audioReaction;

                    mesh.position.x = 0 * (1 - t) + waveX * t;
                    mesh.position.y = helixY * (1 - t) + 0 * t;
                    mesh.position.z = 0;
                    mesh.scale.x = 1 * (1 - t) + 0.15 * t;
                    mesh.scale.y = 1 * (1 - t) + 0.15 * t;
                    mesh.scale.z = 1 * (1 - t) + finalHeight * t;
                    mesh.rotation.y = (Math.PI / 2 + helixRotY) * (1 - t) + 0 * t;
                    mesh.rotation.x = 0 * (1 - t) + (Math.PI / 2) * t;
                    mesh.rotation.z = 0;
                }
            });
        }
    });

    const ringGeometry = useMemo(() => {
        const shape = new THREE.Shape();
        const radius = 0.35;
        shape.absarc(0, 0, radius, 0, Math.PI * 2, false);
        const depth = 10;
        const extrudeSettings: THREE.ExtrudeGeometryOptions = {
            depth,
            bevelEnabled: true,
            bevelThickness: 0.05,
            bevelSize: 0.05,
            bevelSegments: 4,
            curveSegments: 64,
        };
        const geometry = new THREE.ExtrudeGeometry(shape, extrudeSettings);
        geometry.translate(0, 0, -depth / 2);
        return geometry;
    }, []);

    const getRingColor = (index: number, total: number) => {
        const t = (index + levelsDown) / total;
        const r = Math.floor(26 + t * 20);
        const g = Math.floor(26 + t * 30);
        const b = Math.floor(46 + t * 40);
        return `rgb(${r}, ${g}, ${b})`;
    };

    const elements = [];
    const totalRings = levelsUp + levelsDown + 1;
    for (let i = -levelsDown; i <= levelsUp; i++) {
        elements.push({ id: `helix-ring-${i}`, y: i * stepY, rotation: i * rotationStep, index: i + levelsDown });
    }

    return (
        <group ref={groupRef} position={[5, 0, 0]}>
            {elements.map((el, idx) => (
                <mesh
                    key={el.id}
                    ref={(ref: THREE.Mesh | null) => { if (ref) meshRefs.current[idx] = ref; }}
                    geometry={ringGeometry}
                    position={[0, el.y, 0]}
                    rotation={[0, Math.PI / 2 + el.rotation, 0]}
                    castShadow
                >
                    <meshPhysicalMaterial
                        color={getRingColor(el.index, totalRings)}
                        metalness={0.7}
                        roughness={0.5}
                        clearcoat={isActive ? 0.3 : 0}
                        clearcoatRoughness={0.15}
                        reflectivity={isActive ? 0.3 : 0}
                        iridescence={0.96}
                        iridescenceIOR={1.5}
                        iridescenceThicknessRange={[100, 400]}
                        emissive={getRingColor(el.index, totalRings)}
                        emissiveIntensity={isActive ? 0.08 + audioLevel * 0.2 : 0}
                    />
                </mesh>
            ))}
        </group>
    );
};

const Scene: React.FC<{ aiState: AIState; audioLevel: number }> = ({ aiState, audioLevel }) => (
    <Canvas
        className="h-full w-full"
        orthographic
        shadows
        camera={{ zoom: 70, position: [0, 0, 20], near: 0.1, far: 1000 }}
        gl={{ antialias: true }}
        style={{ background: "#fafafa" }}
    >
        <hemisphereLight color={"#e0e0e0"} groundColor={"#ffffff"} intensity={2} />
        <directionalLight position={[10, 10, 5]} intensity={2} castShadow color={"#ffffff"} />
        <HelixRings aiState={aiState} audioLevel={audioLevel} />
        <EffectComposer multisampling={8}>
            <Bloom kernelSize={3} luminanceThreshold={0} luminanceSmoothing={0.4} intensity={0.6 + audioLevel * 0.3} />
            <Bloom kernelSize={KernelSize.HUGE} luminanceThreshold={0} luminanceSmoothing={0} intensity={0.5 + audioLevel * 0.2} />
        </EffectComposer>
    </Canvas>
);

interface HeroProps {
    title: string;
    description: string;
    stats?: Array<{ label: string; value: string }>;
}

export const Hero: React.FC<HeroProps> = ({ title, description, stats }) => {
    const [aiState, setAiState] = useState<AIState>("idle");
    const [audioLevel, setAudioLevel] = useState(0);
    const [error, setError] = useState<string | null>(null);

    const wsRef = useRef<WebSocket | null>(null);
    const audioContextRef = useRef<AudioContext | null>(null);
    const micStreamRef = useRef<MediaStream | null>(null);
    const micAudioContextRef = useRef<AudioContext | null>(null);
    const processorRef = useRef<ScriptProcessorNode | null>(null);
    const animationFrameRef = useRef<number | null>(null);
    const analyserRef = useRef<AnalyserNode | null>(null);

    // Scheduled playback for jitter-free audio (official Web Audio approach)
    const nextStartTimeRef = useRef(0);
    const isPlayingRef = useRef(false);

    const isActive = aiState !== "idle";

    // Official Cartesia recommended sample rate
    const SAMPLE_RATE = 24000;

    // Official jitter-free approach: Schedule audio buffers sequentially
    // Each buffer starts exactly when the previous one ends
    const scheduleAudioPlayback = useCallback((audioData: ArrayBuffer) => {
        try {
            if (!audioContextRef.current || audioContextRef.current.state === 'closed') {
                audioContextRef.current = new AudioContext({ sampleRate: SAMPLE_RATE });
                nextStartTimeRef.current = audioContextRef.current.currentTime;
            }

            const ctx = audioContextRef.current;

            // Resume context if suspended (browser autoplay policy)
            if (ctx.state === 'suspended') {
                ctx.resume();
            }

            // Convert pcm_f32le (32-bit float little-endian) to Float32Array
            // 4 bytes per sample
            const float32Data = new Float32Array(audioData.byteLength / 4);
            const view = new DataView(audioData);
            for (let i = 0; i < float32Data.length; i++) {
                float32Data[i] = view.getFloat32(i * 4, true); // true = little-endian
            }

            // Create audio buffer
            const audioBuffer = ctx.createBuffer(1, float32Data.length, SAMPLE_RATE);
            audioBuffer.getChannelData(0).set(float32Data);

            // Create source and analyser for visualization
            const source = ctx.createBufferSource();
            source.buffer = audioBuffer;

            const analyser = ctx.createAnalyser();
            analyser.fftSize = 256;
            source.connect(analyser);
            analyser.connect(ctx.destination);

            // Schedule playback: start at next available time slot
            const startTime = Math.max(ctx.currentTime, nextStartTimeRef.current);
            source.start(startTime);

            // Update next start time for seamless playback
            nextStartTimeRef.current = startTime + audioBuffer.duration;
            isPlayingRef.current = true;

            // Track audio level for visualization
            const dataArray = new Uint8Array(analyser.frequencyBinCount);
            const trackLevel = () => {
                if (ctx.currentTime < nextStartTimeRef.current) {
                    analyser.getByteFrequencyData(dataArray);
                    const average = dataArray.reduce((a, b) => a + b, 0) / dataArray.length;
                    setAudioLevel(Math.min(1, average / 100));
                    requestAnimationFrame(trackLevel);
                } else {
                    setAudioLevel(0);
                }
            };
            trackLevel();

            source.onended = () => {
                // Check if this was the last scheduled buffer
                if (ctx.currentTime >= nextStartTimeRef.current - 0.05) {
                    isPlayingRef.current = false;
                    setAudioLevel(0);
                    setAiState("listening");
                }
            };

        } catch (err) {
            console.error("Audio playback error:", err);
        }
    }, []);

    const startMicrophone = useCallback(async () => {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({
                audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true }
            });
            micStreamRef.current = stream;

            const audioContext = new AudioContext({ sampleRate: 16000 });
            micAudioContextRef.current = audioContext;
            const source = audioContext.createMediaStreamSource(stream);

            const analyser = audioContext.createAnalyser();
            analyser.fftSize = 256;
            source.connect(analyser);
            analyserRef.current = analyser;
            const dataArray = new Uint8Array(analyser.frequencyBinCount);

            const updateLevel = () => {
                if (analyserRef.current && !isPlayingRef.current) {
                    analyserRef.current.getByteFrequencyData(dataArray);
                    const average = dataArray.reduce((a, b) => a + b, 0) / dataArray.length;
                    setAudioLevel(Math.min(1, average / 128));
                }
                animationFrameRef.current = requestAnimationFrame(updateLevel);
            };
            updateLevel();

            const processor = audioContext.createScriptProcessor(4096, 1, 1);
            processorRef.current = processor;

            processor.onaudioprocess = (event) => {
                if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
                const inputData = event.inputBuffer.getChannelData(0);
                const pcmData = new Int16Array(inputData.length);
                for (let i = 0; i < inputData.length; i++) {
                    const s = Math.max(-1, Math.min(1, inputData[i]));
                    pcmData[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
                }
                wsRef.current.send(pcmData.buffer);
            };

            source.connect(processor);
            processor.connect(audioContext.destination);
        } catch {
            setError("Microphone access denied");
        }
    }, []);

    const stopMicrophone = useCallback(() => {
        if (animationFrameRef.current) cancelAnimationFrame(animationFrameRef.current);
        if (processorRef.current) { processorRef.current.disconnect(); processorRef.current = null; }
        if (micAudioContextRef.current) { micAudioContextRef.current.close(); micAudioContextRef.current = null; }
        if (micStreamRef.current) { micStreamRef.current.getTracks().forEach(track => track.stop()); micStreamRef.current = null; }
        analyserRef.current = null;
        setAudioLevel(0);
    }, []);

    const handleMessage = useCallback(async (event: MessageEvent) => {
        if (event.data instanceof Blob) {
            const arrayBuffer = await event.data.arrayBuffer();
            setAiState("speaking");
            // Use scheduled playback for jitter-free audio
            scheduleAudioPlayback(arrayBuffer);
        } else {
            const data = JSON.parse(event.data);
            switch (data.type) {
                case "ready":
                    setAiState("listening");
                    startMicrophone();
                    break;
                case "transcript":
                    if (data.is_final && data.text) setAiState("processing");
                    break;
                case "llm_response":
                    setAiState("speaking");
                    break;
                case "turn_complete":
                    // Audio completion is handled by scheduled playback
                    break;
                case "barge_in":
                case "tts_interrupted":
                    // Stop all scheduled audio
                    if (audioContextRef.current) {
                        audioContextRef.current.close();
                        audioContextRef.current = null;
                    }
                    nextStartTimeRef.current = 0;
                    isPlayingRef.current = false;
                    setAiState("listening");
                    break;
                case "error":
                    setError(data.message);
                    break;
            }
        }
    }, [scheduleAudioPlayback, startMicrophone]);

    const endSession = useCallback(() => {
        stopMicrophone();
        if (wsRef.current) {
            try { wsRef.current.send(JSON.stringify({ type: "end_call" })); } catch { /* ignore */ }
            wsRef.current.close();
            wsRef.current = null;
        }
        if (audioContextRef.current) { audioContextRef.current.close(); audioContextRef.current = null; }
        nextStartTimeRef.current = 0;
        isPlayingRef.current = false;
        setAiState("idle");
        setAudioLevel(0);
    }, [stopMicrophone]);

    const startSession = useCallback(() => {
        setAiState("connecting");
        setError(null);
        const sessionId = `ask-ai-${Date.now()}`;
        const ws = new WebSocket(`ws://localhost:8000/api/v1/ws/ask-ai/${sessionId}`);
        wsRef.current = ws;

        ws.onopen = () => console.log("Ask AI connected");
        ws.onmessage = handleMessage;
        ws.onerror = () => { setError("Connection error"); endSession(); };
        ws.onclose = () => { if (aiState !== "idle") endSession(); };
    }, [handleMessage, aiState, endSession]);

    const handleClick = useCallback(() => {
        if (aiState === "idle") {
            startSession();
        } else {
            endSession();
        }
    }, [aiState, startSession, endSession]);

    useEffect(() => {
        return () => {
            stopMicrophone();
            if (wsRef.current) wsRef.current.close();
            if (audioContextRef.current) audioContextRef.current.close();
        };
    }, [stopMicrophone]);

    const getStatusText = () => {
        switch (aiState) {
            case "connecting": return "Connecting...";
            case "listening": return "Listening...";
            case "processing": return "Thinking...";
            case "speaking": return "Speaking...";
            default: return "Click to talk";
        }
    };

    return (
        <section className="relative h-screen w-screen font-sans tracking-tight text-gray-900 bg-neutral-50 overflow-hidden">
            <div className="absolute inset-0 z-0">
                <Scene aiState={aiState} audioLevel={audioLevel} />
            </div>

            {/* Ask AI Button - Simple, clean design */}
            <div
                className="absolute z-20 flex items-center justify-center"
                style={{
                    left: '50%',
                    top: '50%',
                    transform: 'translate(calc(-50% + 22.5vw), -50%)'
                }}
            >
                <button
                    onClick={handleClick}
                    className={`relative rounded-full flex flex-col items-center justify-center transition-all duration-500 ease-out cursor-pointer group backdrop-blur-md ${!isActive
                        ? "w-32 h-32 bg-white/60 hover:bg-white/80 border border-gray-200/50 hover:border-gray-300 shadow-2xl hover:shadow-3xl hover:scale-105"
                        : "w-36 h-36 bg-white/80 border-2 border-indigo-300/60"
                        }`}
                    style={{
                        boxShadow: isActive
                            ? `0 0 40px rgba(99, 102, 241, ${0.2 + audioLevel * 0.2}), 0 0 80px rgba(129, 140, 248, ${0.1 + audioLevel * 0.15})`
                            : "0 25px 50px -12px rgba(0, 0, 0, 0.15)",
                    }}
                >
                    {isActive && (
                        <div className="absolute inset-0 rounded-full border-2 border-indigo-400/30" style={{ animation: "ping 2s cubic-bezier(0, 0, 0.2, 1) infinite" }} />
                    )}

                    <div className="text-center z-10">
                        <h3 className={`font-semibold mb-1 ${isActive ? "text-lg text-indigo-700" : "text-xl text-gray-800 group-hover:text-gray-900"}`}>
                            Ask AI
                        </h3>
                        <p className={`text-xs ${isActive ? "text-indigo-500" : "text-gray-500 group-hover:text-gray-600"}`}>
                            {getStatusText()}
                        </p>

                        {/* Simple audio visualization */}
                        {isActive && (
                            <div className="flex items-end justify-center gap-1 h-4 mt-2">
                                {[...Array(5)].map((_, i) => (
                                    <div
                                        key={i}
                                        className="w-1 rounded-full bg-indigo-500 transition-all duration-75"
                                        style={{
                                            height: `${Math.max(3, 4 + audioLevel * 12 + Math.sin(Date.now() / 100 + i) * 2)}px`,
                                            opacity: 0.7 + audioLevel * 0.3,
                                        }}
                                    />
                                ))}
                            </div>
                        )}
                    </div>
                </button>

                {error && <p className="absolute -bottom-10 left-1/2 -translate-x-1/2 text-xs text-red-500 whitespace-nowrap">{error}</p>}
            </div>

            {/* Hero content */}
            <div className="absolute bottom-8 left-8 md:bottom-16 md:left-16 z-20 max-w-2xl">
                <div className="flex flex-col gap-2 mb-6">
                    <MagneticText text="AI VOICE" hoverText="SMART AI" />
                    <MagneticText text="DIALER" hoverText="CALLS" />
                </div>
                <p className="text-gray-600 text-base md:text-lg leading-relaxed font-light tracking-tight mb-8 max-w-lg">
                    {description}
                </p>
                {stats && stats.length > 0 && (
                    <div className="flex flex-wrap gap-8">
                        {stats.map((stat, index) => (
                            <div key={index} className="text-left">
                                <div className="text-3xl md:text-4xl font-semibold text-gray-900">{stat.value}</div>
                                <div className="text-sm text-gray-500 uppercase tracking-wide mt-1">{stat.label}</div>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            <BlurEffect className="absolute bg-gradient-to-b from-transparent to-neutral-50/40 h-1/2 md:h-1/3 w-full bottom-0" intensity={50} />
            <BlurEffect className="absolute bg-gradient-to-b from-neutral-50/40 to-transparent h-1/2 md:h-1/3 w-full top-0" intensity={50} />

            <style jsx>{`
                @keyframes ping {
                    75%, 100% { transform: scale(1.15); opacity: 0; }
                }
            `}</style>
        </section>
    );
};

export type { AIState };
export default Hero;
