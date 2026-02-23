import * as React from "react"
import { Slot } from "@radix-ui/react-slot"

import { cn } from "@/lib/utils"

const Card = React.forwardRef<
    HTMLDivElement,
    React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
    <div
        ref={ref}
        className={cn(
            "rounded-xl border border-gray-200 bg-white text-gray-900 shadow-sm transition-[transform,background-color,box-shadow,border-color] duration-150 ease-out hover:-translate-y-0.5 hover:bg-gray-50 hover:shadow-md",
            className
        )}
        {...props}
    />
))
Card.displayName = "Card"

const CardHeader = React.forwardRef<
    HTMLDivElement,
    React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
    <div
        ref={ref}
        className={cn("flex flex-col space-y-1.5 p-6", className)}
        {...props}
    />
))
CardHeader.displayName = "CardHeader"

export interface CardTitleProps extends React.ComponentPropsWithoutRef<"h2"> {
    asChild?: boolean
}

const CardTitle = React.forwardRef<React.ElementRef<"h2">, CardTitleProps>(
    ({ className, asChild = false, ...props }, ref) => {
        const Comp = asChild ? Slot : "h2"
        return (
            <Comp
                ref={ref}
                className={cn("font-semibold text-xl leading-none tracking-tight", className)}
                {...props}
            />
        )
    }
)
CardTitle.displayName = "CardTitle"

export interface CardDescriptionProps extends React.ComponentPropsWithoutRef<"p"> {
    asChild?: boolean
}

const CardDescription = React.forwardRef<React.ElementRef<"p">, CardDescriptionProps>(
    ({ className, asChild = false, ...props }, ref) => {
        const Comp = asChild ? Slot : "p"
        return (
            <Comp
                ref={ref}
                className={cn("text-sm text-muted-foreground", className)}
                {...props}
            />
        )
    }
)
CardDescription.displayName = "CardDescription"

const CardContent = React.forwardRef<
    HTMLDivElement,
    React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
    <div ref={ref} className={cn("p-6 pt-0", className)} {...props} />
))
CardContent.displayName = "CardContent"

const CardFooter = React.forwardRef<
    HTMLDivElement,
    React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
    <div
        ref={ref}
        className={cn("flex items-center p-6 pt-0", className)}
        {...props}
    />
))
CardFooter.displayName = "CardFooter"

export { Card, CardHeader, CardFooter, CardTitle, CardDescription, CardContent }
