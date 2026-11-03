# 04: Native Desktop Preview Integration

**What to build:**
Integrate diagram rendering into the desktop presentation pipeline. Extend `MarkdownNodeDTO` with diagram metadata, wire diagram pre-rendering in `MarkdownViewerService`, implement `DiagramImageProvider` serving `image://diagram/{theme}/{hash}`, update `MarkdownDocumentModel`, and add `diagramComponent` to `MarkdownNodeDelegate.qml`.

**Blocked by:**
03: Diagram Rendering Port and Infrastructure Adapter

**Status:**
ready-for-agent

- [ ] Extend `MarkdownNodeDTO` in `application/dto/markdown_dto.py` with diagram presentation properties.
- [ ] Update `MarkdownViewerService` to extract diagrams, invoke `IDiagramRenderer` pre-rendering, and project diagram nodes.
- [ ] Implement `DiagramImageProvider` in `interfaces/desktop/providers/diagram_image_provider.py`.
- [ ] Update `MarkdownDocumentModel` with diagram roles (`diagramHash`, `diagramStatus`, `hasError`, `errorMessage`).
- [ ] Update `MarkdownNodeDelegate.qml` with `diagramComponent` rendering images and error states.
- [ ] Wire dependencies in `interfaces/desktop/composition.py`.
- [ ] Add unit and offscreen integration tests in `tests/unit/test_diagram_preview.py`.
