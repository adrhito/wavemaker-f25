@echo off
echo ========================================
echo WaveMaker Documentation PDF Generator
echo ========================================
echo.

echo Generating PDFs for all README files...
echo.

python generate_pdf.py README_PARAMETER_REFERENCE.md README_PARAMETER_REFERENCE.pdf
echo.

python generate_pdf.py README_COMPLETE_PIPELINE.md README_COMPLETE_PIPELINE.pdf
echo.

python generate_pdf.py README_PARAMETER_FLOW.md README_PARAMETER_FLOW.pdf
echo.

python generate_pdf.py README_VERIFICATION_SYSTEM.md README_VERIFICATION_SYSTEM.pdf
echo.

python generate_pdf.py README_DOCUMENTATION_INDEX.md README_DOCUMENTATION_INDEX.pdf
echo.

echo ========================================
echo Done!
echo ========================================
echo.
echo Generated PDF files:
echo - README_PARAMETER_REFERENCE.pdf
echo - README_COMPLETE_PIPELINE.pdf
echo - README_PARAMETER_FLOW.pdf
echo - README_VERIFICATION_SYSTEM.pdf
echo - README_DOCUMENTATION_INDEX.pdf
echo.

pause
