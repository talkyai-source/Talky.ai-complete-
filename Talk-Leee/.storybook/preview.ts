import type { Preview } from "@storybook/react";
import "../src/app/globals.css";

const preview: Preview = {
    parameters: {
        controls: { expanded: true },
        layout: "fullscreen",
    },
};

export default preview;

