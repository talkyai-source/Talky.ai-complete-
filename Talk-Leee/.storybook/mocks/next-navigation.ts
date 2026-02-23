export function useRouter() {
    return {
        push: (_href: string) => {},
        replace: (_href: string) => {},
        back: () => {},
        forward: () => {},
        refresh: () => {},
        prefetch: async (_href: string) => {},
    };
}

export function usePathname() {
    return "/";
}

export function useSearchParams() {
    return new URLSearchParams();
}

export function useParams() {
    return {};
}

