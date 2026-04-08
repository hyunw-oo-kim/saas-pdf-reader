/**
 * Apryse WebViewer initialization utility.
 *
 * Supports:
 * - SAS Token URL loading
 * - Page navigation, zoom, rotation
 * - Lazy loading for 100+ page documents
 * - Read-only mode for Viewer role
 * - Annotation tools (highlight, underline, strikeout, sticky note, rectangle, ellipse, freehand)
 * - XFDF import/export for annotation persistence
 */

import type WebViewerInstance from "@pdftron/webviewer";

export type WebViewerInstanceType = WebViewerInstance.WebViewerInstance;

export interface InitWebViewerOptions {
  readOnly?: boolean;
}

export async function initWebViewer(
  element: HTMLElement,
  documentUrl: string,
  options?: InitWebViewerOptions,
): Promise<WebViewerInstanceType> {
  const WebViewer = (await import("@pdftron/webviewer")).default;

  const isReadOnly = options?.readOnly ?? false;

  const instance = await WebViewer(
    {
      path: "/webviewer",
      initialDoc: documentUrl,
      // Enable streaming for lazy loading of large documents
      streaming: true,
      disabledElements: isReadOnly
        ? ["toolsHeader", "annotationPopup"]
        : [],
      // Enable page navigation, zoom, rotation by default
      fullAPI: false,
    },
    element,
  );

  // Enable annotation tools when not read-only
  if (!isReadOnly) {
    instance.UI.enableFeatures([instance.UI.Feature.Annotations]);
  } else {
    instance.UI.disableFeatures([instance.UI.Feature.Annotations]);
  }

  return instance;
}

/**
 * Load a new document into an existing WebViewer instance.
 * Used for SAS token refresh — swaps the document URL without
 * re-initializing the entire viewer.
 */
export function loadDocument(
  instance: WebViewerInstanceType,
  newUrl: string,
): void {
  instance.UI.loadDocument(newUrl, { filename: "document.pdf" });
}

/**
 * Import XFDF annotation data into the viewer.
 * Used to restore saved annotations when opening a document.
 */
export async function importAnnotations(
  instance: WebViewerInstanceType,
  xfdfString: string,
): Promise<void> {
  const { annotationManager } = instance.Core;
  await annotationManager.importAnnotations(xfdfString);
}

/**
 * Export all annotations from the viewer as XFDF string.
 * Used to save annotations to the backend.
 */
export async function exportAnnotations(
  instance: WebViewerInstanceType,
): Promise<string> {
  const { annotationManager } = instance.Core;
  return annotationManager.exportAnnotations();
}
