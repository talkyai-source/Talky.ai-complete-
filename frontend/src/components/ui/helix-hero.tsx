"use client";

import { Canvas, useFrame } from "@react-three/fiber";
import { Bloom, EffectComposer } from "@react-three/postprocessing";
import { KernelSize } from "postprocessing";
import type React from "react";
import { useMemo, useRef, useState, useCallback, useEffect } from "react";
import * as THREE from "three";
import BlurEffect from "react-progressive-blur";
import { MagneticText } from "./morphing-cursor";

type AIState = "idle" | "connecting" | "browsing" | "listening" | "processing" | "speaking";

interface VoiceAgent {
    id: string;
    name: string;
    gender: string;
    description: string;
}

const VOICE_AGENTS: VoiceAgent[] = [
    { id: "sophia", name: "Sophia", gender: "female", description: "Warm & Professional" },
    { id: "emma", name: "Emma", gender: "female", description: "Energetic & Friendly" },
    { id: "alex", name: "Alex", gender: "male", description: "Confident & Clear" },
];

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

            // Smooth transition (0 = helix, 1 = sound wave) - FAST
            const targetTransition = isActive ? 1 : 0;
            transitionRef.current += (targetTransition - transitionRef.current) * 0.15; // Faster!
            const t = transitionRef.current;

            // ROTATE ONLY WHEN IDLE (helix mode) - stop when active (wave mode)
            if (!isActive) {
                groupRef.current.rotation.y += 0.005;
            }

            // STAY IN RIGHT SECTION - don't move!
            groupRef.current.position.x = 5;
            groupRef.current.position.y = 0;
            groupRef.current.position.z = 0;

            // Animate rings from helix to SOUND WAVE pattern
            const totalRings = levelsUp + levelsDown + 1;

            meshRefs.current.forEach((mesh, index) => {
                if (mesh) {
                    // Original helix position
                    const helixY = (index - levelsDown) * stepY;
                    const helixRotY = (index - levelsDown) * rotationStep;

                    // SOUND WAVE pattern: bars spread horizontally, height varies with audio
                    const waveSpacing = 0.5;
                    const centerIndex = totalRings / 2;
                    const distanceFromCenter = Math.abs(index - centerIndex);

                    // X position: spread rings horizontally across the wave
                    const waveX = (index - centerIndex) * waveSpacing;

                    // Height of each bar based on REAL audio level only
                    // Bars in center are taller, edges are shorter (like real waveform)
                    const baseHeight = 0.15; // Static minimum height when silent
                    const maxHeight = 2.5;
                    const falloff = 1 - (distanceFromCenter / (totalRings / 2)) * 0.85;

                    // ONLY react to real audio - no random motion!
                    const audioReaction = audioLevel > 0.01 ? audioLevel * maxHeight * falloff : 0;
                    const finalHeight = baseHeight + audioReaction;

                    // Lerp positions - all bars at Y=0 in wave mode
                    mesh.position.x = 0 * (1 - t) + waveX * t;
                    mesh.position.y = helixY * (1 - t) + 0 * t;
                    mesh.position.z = 0;

                    // Scale: make bars stretch along their tube length (Z axis when facing camera)
                    // Tube is originally along Z axis, so scale.z = height
                    mesh.scale.x = 1 * (1 - t) + 0.15 * t; // Thin bars
                    mesh.scale.y = 1 * (1 - t) + 0.15 * t; // Thin bars
                    mesh.scale.z = 1 * (1 - t) + finalHeight * t; // Height = length of tube

                    // Rotation: in wave mode, tubes face camera (point along Z axis toward viewer)
                    // rotation.y = 0 makes tube point toward camera, then we tilt it up
                    mesh.rotation.y = (Math.PI / 2 + helixRotY) * (1 - t) + 0 * t; // Face camera
                    mesh.rotation.x = 0 * (1 - t) + (Math.PI / 2) * t; // Tilt up so tube points UP (Y axis)
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

    // Original colors (no color shift)
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

const AudioVisualizer: React.FC<{ isActive: boolean; audioLevel: number }> = ({ isActive, audioLevel }) => {
    if (!isActive) return null;
    return (
        <div className="flex items-end justify-center gap-1 h-5 mt-1">
            {[...Array(5)].map((_, i) => (
                <div
                    key={i}
                    className="w-1 rounded-full transition-all duration-75"
                    style={{
                        height: `${Math.max(3, 4 + Math.random() * audioLevel * 12 + Math.sin(Date.now() / 100 + i) * 2)}px`,
                        background: `linear-gradient(to top, #6366f1, #818cf8, #a5b4fc)`,
                        opacity: 0.8 + audioLevel * 0.2,
                    }}
                />
            ))}
        </div>
    );
};

interface HeroProps {
    title: string;
    description: string;
    stats?: Array<{ label: string; value: string }>;
}

export const Hero: React.FC<HeroProps> = ({ title, description, stats }) => {
    const [aiState, setAiState] = useState<AIState>("idle");
    const [audioLevel, setAudioLevel] = useState(0);
    const [error, setError] = useState<string | null>(null);
    const [selectedVoiceIndex, setSelectedVoiceIndex] = useState(0);
    const [currentVoiceName, setCurrentVoiceName] = useState("");
    const [voiceSelected, setVoiceSelected] = useState(false);
    const [hasSwiped, setHasSwiped] = useState(false);

    const wsRef = useRef<WebSocket | null>(null);
    const audioContextRef = useRef<AudioContext | null>(null);
    const audioQueueRef = useRef<ArrayBuffer[]>([]);
    const isPlayingRef = useRef(false);
    const micStreamRef = useRef<MediaStream | null>(null);
    const micAudioContextRef = useRef<AudioContext | null>(null);
    const processorRef = useRef<ScriptProcessorNode | null>(null);
    const animationFrameRef = useRef<number | null>(null);
    const analyserRef = useRef<AnalyserNode | null>(null);

    const selectedVoice = VOICE_AGENTS[selectedVoiceIndex];
    const isActive = aiState !== "idle";

    const playNextAudioChunk = useCallback(async () => {
        // Start IMMEDIATELY with first chunk - no pre-buffering delay
        // Backend sends small first chunk (~100ms) for instant audio start
        if (isPlayingRef.current || audioQueueRef.current.length === 0) return;
        isPlayingRef.current = true;

        try {
            if (!audioContextRef.current || audioContextRef.current.state === 'closed') {
                audioContextRef.current = new AudioContext({ sampleRate: 16000 });
            }
            const ctx = audioContextRef.current;
            const buffer = audioQueueRef.current.shift();

            if (buffer) {
                const float32Data = new Float32Array(buffer.byteLength / 4);
                const view = new DataView(buffer);
                for (let i = 0; i < float32Data.length; i++) {
                    float32Data[i] = view.getFloat32(i * 4, true);
                }
                const audioBuffer = ctx.createBuffer(1, float32Data.length, 16000);
                audioBuffer.getChannelData(0).set(float32Data);

                // Create analyser for output audio visualization
                const analyser = ctx.createAnalyser();
                analyser.fftSize = 256;

                const source = ctx.createBufferSource();
                source.buffer = audioBuffer;
                source.connect(analyser);
                analyser.connect(ctx.destination);
                source.start();

                // Track output audio level while playing
                const dataArray = new Uint8Array(analyser.frequencyBinCount);
                const trackOutputLevel = () => {
                    if (isPlayingRef.current) {
                        analyser.getByteFrequencyData(dataArray);
                        const average = dataArray.reduce((a, b) => a + b, 0) / dataArray.length;
                        setAudioLevel(Math.min(1, average / 100));
                        requestAnimationFrame(trackOutputLevel);
                    }
                };
                trackOutputLevel();

                source.onended = () => {
                    isPlayingRef.current = false;
                    setAudioLevel(0); // Reset level when chunk ends
                    playNextAudioChunk();
                };
            } else {
                isPlayingRef.current = false;
            }
        } catch {
            isPlayingRef.current = false;
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
                if (analyserRef.current) {
                    analyserRef.current.getByteFrequencyData(dataArray);
                    const average = dataArray.reduce((a, b) => a + b, 0) / dataArray.length;
                    setAudioLevel(Math.min(1, average / 128));
                    animationFrameRef.current = requestAnimationFrame(updateLevel);
                }
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
            audioQueueRef.current.push(arrayBuffer);
            setAiState("speaking");
            playNextAudioChunk();
        } else {
            const data = JSON.parse(event.data);
            switch (data.type) {
                case "ready":
                    setAiState("browsing");
                    setCurrentVoiceName(data.agent_name);
                    break;
                case "voice_switched":
                    setCurrentVoiceName(data.agent_name);
                    break;
                case "transcript":
                    if (data.is_final && data.text) setAiState("processing");
                    break;
                case "llm_response":
                    setAiState("speaking");
                    break;
                case "turn_complete":
                    if (voiceSelected) {
                        setAiState("listening");
                    } else {
                        setAiState("browsing");
                    }
                    break;
                case "barge_in":
                case "tts_interrupted":
                    audioQueueRef.current = [];
                    isPlayingRef.current = false;
                    if (audioContextRef.current) { audioContextRef.current.close(); audioContextRef.current = null; }
                    if (voiceSelected) setAiState("listening");
                    break;
                case "error":
                    setError(data.message);
                    break;
            }
        }
    }, [playNextAudioChunk, voiceSelected]);

    const endSession = useCallback(() => {
        stopMicrophone();
        if (wsRef.current) {
            try { wsRef.current.send(JSON.stringify({ type: "end_call" })); } catch { /* ignore */ }
            wsRef.current.close();
            wsRef.current = null;
        }
        if (audioContextRef.current) { audioContextRef.current.close(); audioContextRef.current = null; }
        audioQueueRef.current = [];
        isPlayingRef.current = false;
        setAiState("idle");
        setAudioLevel(0);
        setCurrentVoiceName("");
        setHasSwiped(false);
        setVoiceSelected(false);
    }, [stopMicrophone]);

    const startSession = useCallback(() => {
        setAiState("connecting");
        setError(null);
        setHasSwiped(false);
        setVoiceSelected(false);
        const sessionId = `ask-ai-${Date.now()}`;
        const ws = new WebSocket(`ws://localhost:8000/api/v1/ws/ai-test/${sessionId}`);
        wsRef.current = ws;

        ws.onopen = () => {
            ws.send(JSON.stringify({ type: "config", voice_id: selectedVoice.id }));
        };
        ws.onmessage = handleMessage;
        ws.onerror = () => { setError("Connection error"); endSession(); };
        ws.onclose = () => { if (aiState !== "idle") endSession(); };
    }, [handleMessage, selectedVoice.id, aiState, endSession]);

    const selectVoice = useCallback(() => {
        setVoiceSelected(true);
        setAiState("listening");
        startMicrophone();
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
            wsRef.current.send(JSON.stringify({ type: "voice_selected", voice_id: selectedVoice.id }));
        }
    }, [selectedVoice.id, startMicrophone]);

    const switchVoice = useCallback((direction: 'prev' | 'next') => {
        if (!hasSwiped) setHasSwiped(true);

        const newIndex = direction === 'next'
            ? (selectedVoiceIndex + 1) % VOICE_AGENTS.length
            : (selectedVoiceIndex - 1 + VOICE_AGENTS.length) % VOICE_AGENTS.length;
        setSelectedVoiceIndex(newIndex);

        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
            audioQueueRef.current = [];
            isPlayingRef.current = false;
            if (audioContextRef.current) { audioContextRef.current.close(); audioContextRef.current = null; }
            wsRef.current.send(JSON.stringify({ type: "switch_voice", voice_id: VOICE_AGENTS[newIndex].id }));
        }
    }, [selectedVoiceIndex, hasSwiped]);

    const handleMainButtonClick = useCallback(() => {
        if (aiState === "idle") {
            startSession();
        } else if (aiState === "browsing" || aiState === "speaking") {
            selectVoice();
        } else {
            endSession();
        }
    }, [aiState, startSession, selectVoice, endSession]);

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
            case "browsing": return "Tap to select";
            case "listening": return "Listening...";
            case "processing": return "Thinking...";
            case "speaking": return voiceSelected ? "Speaking..." : "Tap to select";
            default: return "Click to talk";
        }
    };

    const showSwipeArrows = isActive && !voiceSelected;

    return (
        <section className="relative h-screen w-screen font-sans tracking-tight text-gray-900 bg-neutral-50 overflow-hidden">
            <div className="absolute inset-0 z-0">
                <Scene aiState={aiState} audioLevel={audioLevel} />
            </div>

            {/* Ask AI Button - Always at center of waveform/helix (right side) */}
            <div
                className="absolute z-20 flex items-center gap-3"
                style={{
                    left: '50%',
                    top: '50%',
                    transform: 'translate(calc(-50% + 22.5vw), -50%)'
                }}
            >
                {/* Left Arrow */}
                {showSwipeArrows && hasSwiped && (
                    <button
                        onClick={() => switchVoice('prev')}
                        className="w-10 h-10 rounded-full bg-white/90 hover:bg-white border border-indigo-200 flex items-center justify-center text-indigo-600 hover:text-indigo-700 transition-all shadow-lg hover:shadow-xl hover:scale-110"
                    >
                        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" /></svg>
                    </button>
                )}

                {/* Main Circle Button */}
                <button
                    onClick={handleMainButtonClick}
                    className={`relative rounded-full flex flex-col items-center justify-center transition-all duration-500 ease-out cursor-pointer group backdrop-blur-md ${!isActive
                        ? "w-32 h-32 bg-white/60 hover:bg-white/80 border border-gray-200/50 hover:border-gray-300 shadow-2xl hover:shadow-3xl hover:scale-105"
                        : "w-40 h-40 bg-white/80 border-2 border-indigo-300/60"
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
                        {!isActive && (
                            <>
                                <h3 className="text-xl font-semibold text-gray-800 group-hover:text-gray-900 mb-1">Ask AI</h3>
                                <p className="text-xs text-gray-500 group-hover:text-gray-600">{getStatusText()}</p>
                            </>
                        )}

                        {isActive && (
                            <>
                                <div className="text-2xl font-bold text-indigo-700 mb-0.5">
                                    {currentVoiceName || selectedVoice.name}
                                </div>
                                <div className="text-xs text-indigo-500 mb-1">{selectedVoice.description}</div>
                                <AudioVisualizer isActive={voiceSelected && (aiState === "listening" || aiState === "speaking")} audioLevel={audioLevel} />
                                <p className="text-[10px] text-indigo-400 mt-1">{getStatusText()}</p>
                            </>
                        )}
                    </div>
                </button>

                {/* Right Arrow */}
                {showSwipeArrows && (
                    <button
                        onClick={() => switchVoice('next')}
                        className="w-10 h-10 rounded-full bg-white/90 hover:bg-white border border-indigo-200 flex items-center justify-center text-indigo-600 hover:text-indigo-700 transition-all shadow-lg hover:shadow-xl hover:scale-110"
                    >
                        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" /></svg>
                    </button>
                )}

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
