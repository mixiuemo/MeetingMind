import { useEffect, useState } from "react";

function readViewport() {
  const viewport = window.visualViewport;
  return {
    left: viewport?.offsetLeft || 0,
    top: viewport?.offsetTop || 0,
    width: viewport?.width || window.innerWidth,
    height: viewport?.height || window.innerHeight,
  };
}

export default function useAssistantViewport() {
  const [viewport, setViewport] = useState(readViewport);

  useEffect(() => {
    const visualViewport = window.visualViewport;
    function updateViewport() {
      setViewport(readViewport());
    }

    window.addEventListener("resize", updateViewport);
    visualViewport?.addEventListener("resize", updateViewport);
    visualViewport?.addEventListener("scroll", updateViewport);
    return () => {
      window.removeEventListener("resize", updateViewport);
      visualViewport?.removeEventListener("resize", updateViewport);
      visualViewport?.removeEventListener("scroll", updateViewport);
    };
  }, []);

  return viewport;
}
