"use client"

import type React from "react"
import { useRef, useState, useCallback, useEffect } from "react"
import { cn } from "@/lib/utils"

interface MagneticTextProps {
    text: string
    hoverText?: string
    className?: string
    textSpanClassName?: string
    hoverTextSpanClassName?: string
}

export function MagneticText({
    text = "CREATIVE",
    hoverText = "EXPLORE",
    className,
    textSpanClassName,
    hoverTextSpanClassName,
}: MagneticTextProps) {
    const containerRef = useRef<HTMLDivElement>(null)
    const circleRef = useRef<HTMLDivElement>(null)
    const innerTextRef = useRef<HTMLDivElement>(null)
    const [isHovered, setIsHovered] = useState(false)

    const mousePos = useRef({ x: 0, y: 0 })
    const currentPos = useRef({ x: 0, y: 0 })
    const animationFrameRef = useRef<number | undefined>(undefined)

    useEffect(() => {
        const el = containerRef.current
        const inner = innerTextRef.current
        if (!el || !inner) return

        const update = () => {
            inner.style.width = `${el.offsetWidth}px`
            inner.style.height = `${el.offsetHeight}px`
        }

        update()
        const ro = new ResizeObserver(() => update())
        ro.observe(el)
        return () => ro.disconnect()
    }, [])

    useEffect(() => {
        if (!isHovered) return
        const circleEl = circleRef.current
        const innerEl = innerTextRef.current
        const lerp = (start: number, end: number, factor: number) => start + (end - start) * factor

        const animate = () => {
            const dx = mousePos.current.x - currentPos.current.x
            const dy = mousePos.current.y - currentPos.current.y
            if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) {
                animationFrameRef.current = requestAnimationFrame(animate)
                return
            }

            currentPos.current.x = lerp(currentPos.current.x, mousePos.current.x, 0.15)
            currentPos.current.y = lerp(currentPos.current.y, mousePos.current.y, 0.15)

            if (circleEl) {
                circleEl.style.transform = `translate(${currentPos.current.x}px, ${currentPos.current.y}px) translate(-50%, -50%) scale(1)`
            }

            if (innerEl) {
                innerEl.style.transform = `translate(${-currentPos.current.x}px, ${-currentPos.current.y}px)`
            }

            animationFrameRef.current = requestAnimationFrame(animate)
        }

        animationFrameRef.current = requestAnimationFrame(animate)
        return () => {
            if (animationFrameRef.current) cancelAnimationFrame(animationFrameRef.current)
            if (circleEl) {
                circleEl.style.transform = `translate(${currentPos.current.x}px, ${currentPos.current.y}px) translate(-50%, -50%) scale(0)`
            }
        }
    }, [isHovered])

    const handleMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
        if (!containerRef.current) return
        const rect = containerRef.current.getBoundingClientRect()
        mousePos.current = {
            x: e.clientX - rect.left,
            y: e.clientY - rect.top,
        }
    }, [])

    const handleMouseEnter = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
        if (!containerRef.current) return
        const rect = containerRef.current.getBoundingClientRect()
        const x = e.clientX - rect.left
        const y = e.clientY - rect.top
        mousePos.current = { x, y }
        currentPos.current = { x, y }
        setIsHovered(true)
    }, [])

    const handleMouseLeave = useCallback(() => {
        setIsHovered(false)
    }, [])

    return (
        <div
            ref={containerRef}
            onMouseMove={handleMouseMove}
            onMouseEnter={handleMouseEnter}
            onMouseLeave={handleMouseLeave}
            className={cn("relative inline-flex items-center justify-center cursor-none select-none", className)}
        >
            <span
                className={cn(
                    "text-6xl md:text-8xl font-bold tracking-tighter text-primary dark:text-foreground",
                    textSpanClassName
                )}
            >
                {text}
            </span>

            <div
                ref={circleRef}
                className="absolute top-0 left-0 pointer-events-none rounded-full bg-primary dark:bg-foreground overflow-hidden"
                style={{
                    width: 180,
                    height: 180,
                    transform: `translate(${currentPos.current.x}px, ${currentPos.current.y}px) translate(-50%, -50%) scale(${isHovered ? 1 : 0})`,
                    transition: "transform 0.5s cubic-bezier(0.33, 1, 0.68, 1)",
                }}
            >
                <div
                    ref={innerTextRef}
                    className="absolute flex items-center justify-center"
                    style={{
                        top: "50%",
                        left: "50%",
                    }}
                >
                    <span
                        className={cn(
                            "text-6xl md:text-8xl font-bold tracking-tighter text-primary-foreground dark:text-background whitespace-nowrap",
                            hoverTextSpanClassName
                        )}
                    >
                        {hoverText}
                    </span>
                </div>
            </div>
        </div>
    )
}
