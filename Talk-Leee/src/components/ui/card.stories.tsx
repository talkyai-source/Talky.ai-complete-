import type { Meta, StoryObj } from "@storybook/react";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

const meta: Meta<typeof Card> = {
    title: "UI/Card",
    component: Card,
    parameters: {
        docs: {
            description: {
                component:
                    "Visual style guide (tokens-first):\n\n- Colors: use bg-background/bg-card + text-foreground/text-muted-foreground and border-border.\n- Elevation: prefer subtle shadow + border over heavy drop-shadows.\n- Radius: keep rounded-2xl/rounded-xl for surfaces; avoid mixed radii within the same surface.\n\nImplementation assets:\n- Global theme tokens + home surface tokens are defined in src/app/globals.css (CSS variables).\n\nVisual QA checklist:\n- Light/dark mode: text contrast stays readable across surfaces.\n- Focus states: focus-visible ring is clearly visible on interactive elements.\n- Borders/shadows: consistent weight and opacity across cards.\n- Spacing: consistent padding (p-4/p-6) and grid gaps at breakpoints.\n",
            },
        },
    },
};

export default meta;
type Story = StoryObj<typeof Card>;

export const Default: Story = {
    render: () => (
        <div className="p-6 bg-background text-foreground">
            <Card className="max-w-md">
                <CardHeader>
                    <CardTitle>Card title</CardTitle>
                    <CardDescription>Short description goes here.</CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="text-sm text-muted-foreground">
                        Content area with typical body text. Use CardContent for padding and layout.
                    </div>
                </CardContent>
                <CardFooter className="justify-end gap-2">
                    <Button variant="outline">Cancel</Button>
                    <Button>Save</Button>
                </CardFooter>
            </Card>
        </div>
    ),
};
