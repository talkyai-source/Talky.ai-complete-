"use client";

import { Canvas, useFrame } from "@react-three/fiber";
import { Bloom, EffectComposer } from "@react-three/postprocessing";
import { KernelSize } from "postprocessing";
import type React from "react";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import BlurEffect from "react-progressive-blur";
import { MagneticText } from "./morphing-cursor";

interface HelixRingsProps {
    levelsUp?: number;
    levelsDown?: number;
    stepY?: number;
    rotationStep?: number;
}

const HelixRings: React.FC<HelixRingsProps> = ({
    levelsUp = 10,
    levelsDown = 10,
    stepY = 0.85,
    rotationStep = Math.PI / 16,
}) => {
    const groupRef = useRef<THREE.Group>(new THREE.Group());

    useFrame(() => {
        if (groupRef.current) {
            groupRef.current.rotation.y += 0.005;
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

    const elements = [];
    for (let i = -levelsDown; i <= levelsUp; i++) {
        elements.push({
            id: `helix-ring-${i}`,
            y: i * stepY,
            rotation: i * rotationStep,
        });
    }

    return (
        <group
            scale={1}
            position={[5, 0, 0]}
            ref={groupRef}
            rotation={[0, 0, 0]}
        >
            {elements.map((el) => (
                <mesh
                    key={el.id}
                    geometry={ringGeometry}
                    position={[0, el.y, 0]}
                    rotation={[0, Math.PI / 2 + el.rotation, 0]}
                    castShadow
                >
                    <meshPhysicalMaterial
                        color="#1a1a2e"
                        metalness={0.7}
                        roughness={0.5}
                        clearcoat={0}
                        clearcoatRoughness={0.15}
                        reflectivity={0}
                        iridescence={0.96}
                        iridescenceIOR={1.5}
                        iridescenceThicknessRange={[100, 400]}
                    />
                </mesh>
            ))}
        </group>
    );
};

const Scene: React.FC = () => {
    return (
        <Canvas
            className="h-full w-full"
            orthographic
            shadows
            camera={{
                zoom: 70,
                position: [0, 0, 7],
                near: 0.1,
                far: 1000,
            }}
            gl={{ antialias: true }}
            style={{ background: "#fafafa" }}
        >
            <hemisphereLight
                color={"#e0e0e0"}
                groundColor={"#ffffff"}
                intensity={2}
            />

            <directionalLight
                position={[10, 10, 5]}
                intensity={2}
                castShadow
                color={"#ffffff"}
                shadow-mapSize-width={2048}
                shadow-mapSize-height={2048}
            />

            <HelixRings />

            <EffectComposer multisampling={8}>
                <Bloom
                    kernelSize={3}
                    luminanceThreshold={0}
                    luminanceSmoothing={0.4}
                    intensity={0.6}
                />
                <Bloom
                    kernelSize={KernelSize.HUGE}
                    luminanceThreshold={0}
                    luminanceSmoothing={0}
                    intensity={0.5}
                />
            </EffectComposer>
        </Canvas>
    );
};

interface HeroProps {
    title: string;
    description: string;
    stats?: Array<{ label: string; value: string }>;
}

export const Hero: React.FC<HeroProps> = ({ title, description, stats }) => {
    return (
        <section className="relative h-screen w-screen font-sans tracking-tight text-gray-900 bg-neutral-50 overflow-hidden">
            <div className="absolute inset-0 z-0">
                <Scene />
            </div>

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
                                <div className="text-3xl md:text-4xl font-semibold text-gray-900">
                                    {stat.value}
                                </div>
                                <div className="text-sm text-gray-500 uppercase tracking-wide mt-1">
                                    {stat.label}
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            <BlurEffect
                className="absolute bg-gradient-to-b from-transparent to-neutral-50/40 h-1/2 md:h-1/3 w-full bottom-0"
                intensity={50}
            />
            <BlurEffect
                className="absolute bg-gradient-to-b from-neutral-50/40 to-transparent h-1/2 md:h-1/3 w-full top-0"
                intensity={50}
            />
        </section>
    );
};

export default Hero;
