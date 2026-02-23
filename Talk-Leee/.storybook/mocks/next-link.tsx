import type React from "react";

export default function Link(
    props: React.AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }
) {
    const { href, children, ...rest } = props;
    return (
        <a href={href} {...rest}>
            {children}
        </a>
    );
}

