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
            "rounded-xl border border-gray-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-gray-900 dark:text-white shadow-sm transition-[transform,background-color,box-shadow,border-color] duration-150 ease-out hover:-translate-y-0.5 hover:bg-gray-50 dark:hover:bg-slate-800 hover:shadow-md dark:hover:shadow-lg",
            // The hover lift moves the card 2px up, which used to take its bottom 2px out from under a
            // pointer resting there: the card dropped, was hovered again, lifted again, on every frame.
            // While hovered, an invisible strip hangs below the lifted card, exactly over the space it
            // left, so the pointer stays on the card. It starts at the inside of the card's bottom border
            // (where an absolutely placed box is measured from), so it is the 2px of the lift plus the
            // border's own width, which it inherits. It exists only while the card is hovered, and only
            // inside the card's own at-rest box.
            "hover:after:absolute hover:after:inset-x-0 hover:after:top-full hover:after:box-content hover:after:h-0.5 hover:after:border-transparent hover:after:[border-bottom-width:inherit] hover:after:content-['']",
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
