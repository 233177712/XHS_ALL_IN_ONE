import { Image } from "antd";
import type { ImageProps } from "antd";
import { useEffect, useState } from "react";
import type { ImgHTMLAttributes, ReactNode } from "react";

import { createMediaObjectUrl } from "../../lib/api";

const PROTECTED_MEDIA_PATH = "/api/files/media/";

function isProtectedMediaUrl(src: string): boolean {
  if (src.startsWith(PROTECTED_MEDIA_PATH)) {
    return true;
  }

  try {
    const url = new URL(src, window.location.origin);
    return url.origin === window.location.origin && url.pathname.startsWith(PROTECTED_MEDIA_PATH);
  } catch {
    return false;
  }
}

function useResolvedImageSrc(src?: string): string {
  const [resolvedSrc, setResolvedSrc] = useState(() => {
    if (!src || isProtectedMediaUrl(src)) {
      return "";
    }
    return src;
  });

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;

    if (!src) {
      setResolvedSrc("");
      return;
    }

    if (!isProtectedMediaUrl(src)) {
      setResolvedSrc(src);
      return;
    }

    setResolvedSrc("");
    void createMediaObjectUrl(src)
      .then((nextUrl) => {
        if (cancelled) {
          window.URL.revokeObjectURL(nextUrl);
          return;
        }
        objectUrl = nextUrl;
        setResolvedSrc(nextUrl);
      })
      .catch(() => {
        if (!cancelled) {
          setResolvedSrc("");
        }
      });

    return () => {
      cancelled = true;
      if (objectUrl) {
        window.URL.revokeObjectURL(objectUrl);
      }
    };
  }, [src]);

  return resolvedSrc;
}

type AuthImgProps = Omit<ImgHTMLAttributes<HTMLImageElement>, "src"> & {
  src?: string;
  fallbackNode?: ReactNode;
};

export function AuthImg({ src, fallbackNode = null, ...props }: AuthImgProps) {
  const resolvedSrc = useResolvedImageSrc(src);
  if (!src) {
    return null;
  }
  if (!resolvedSrc) {
    return <>{fallbackNode}</>;
  }
  return <img {...props} src={resolvedSrc} />;
}

type AuthImageProps = Omit<ImageProps, "src"> & {
  src?: string;
  fallbackNode?: ReactNode;
};

export function AuthImage({ src, fallbackNode = null, ...props }: AuthImageProps) {
  const resolvedSrc = useResolvedImageSrc(src);
  if (!src) {
    return null;
  }
  if (!resolvedSrc) {
    return <>{fallbackNode}</>;
  }
  return <Image {...props} src={resolvedSrc} />;
}
