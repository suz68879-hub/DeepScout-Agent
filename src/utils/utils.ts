/**
 * Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
 * SPDX-license-identifier: BSD-3-Clause
 */

import { useEffect, useState } from 'react';

export const isMobile = () =>
  /Mobi|Android|iPhone|iPad|Windows Phone/i.test(window.navigator.userAgent) ||
  window?.innerWidth < 767;

export function useIsMobile() {
  const getIsMobile = () =>
    /Mobi|Android|iPhone|iPad|Windows Phone/i.test(window.navigator.userAgent) ||
    window.innerWidth < 767;

  const [isMobile, setIsMobile] = useState(getIsMobile());

  useEffect(() => {
    const handleResize = () => {
      const value = getIsMobile();
      setIsMobile(value);
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  return isMobile;
}
