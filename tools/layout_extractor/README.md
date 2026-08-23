# SVG Dimension Extractor & Grid/Trigonometry Adjustment

This directory contains `svg_dimension_graph.py`, which is the vector-first pass for extracting datasheet footprints and component layouts directly from SVGs (e.g., converted from PDFs).

## How it Fits the Grid/Trigonometry Pipeline

1. **Extraction (`svg_dimension_graph.py`)**
   The tool extracts a "dimension graph" from the SVG:
   - Finds dimension text (e.g., `8.64`, `2x 1.60`, `Pitch=0.50`).
   - Associates each text label with the nearest compatible pair of dimension arrowheads.
   - Casts perpendicular rays 90 degrees outward from the arrow directions toward the component drawing geometry.
   - Stops at the first likely component outline or feature edge.
   - Calculates a local scale factor mapping the SVG units to the actual measured real-world units (mm) specified in the text label.

2. **Grid Assumption**
   Initially, before dimensions are applied, the system makes an assumption about the component's size using a baseline grid. The component outline is mapped to this grid layout. 

3. **Applying Dimensions and Ratios**
   Once the dimension graph provides the exact measured values and their corresponding feature endpoints on the outline:
   - The values (or referenced values from a datasheet table) are applied to the grid points.
   - The grid sizes are adjusted via calculated ratios so that the distances between the feature endpoints exactly match the text labels.

4. **Trigonometric Closed-Shape Adjustment**
   - Finally, through trigonometry and geometric constraint solving, the remaining closed shapes (like pads, holes, and internal component contours) in the footprint are adjusted.
   - Using the now-scaled grid and the angles preserved from the SVG, the layout math resizes and aligns all features to their true dimensional spacing.
