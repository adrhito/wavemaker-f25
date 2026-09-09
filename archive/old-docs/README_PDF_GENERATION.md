# PDF Generation Guide

## How to Generate PDFs from Documentation

This guide explains how to convert the markdown documentation files to PDF format.

---

## 🎯 Quick Start

### Option 1: Using the Batch File (Easiest)

**For All Documentation Files:**
```
Double-click: generate_all_pdfs.bat
```

**For Single File:**
```
python generate_pdf.py README_PARAMETER_REFERENCE.md
```

---

## 📋 Prerequisites

You need ONE of these options installed:

### **Option A: Pandoc (Recommended - Best Quality)**

1. Download from: https://pandoc.org/installing.html
2. Install for Windows
3. Run the script - it will automatically use Pandoc

**Advantages:**
- Best PDF quality
- Professional formatting
- Handles complex markdown
- No Python dependencies needed

---

### **Option B: Python Libraries (Alternative)**

**Install required libraries:**
```powershell
pip install -r pdf_requirements.txt
```

Or manually:
```powershell
pip install markdown weasyprint
```

**Advantages:**
- Works without external tools
- Good quality output
- Python-based, easier to customize

---

## 🚀 Usage

### Generate Single PDF

**Basic usage:**
```powershell
python generate_pdf.py README_PARAMETER_REFERENCE.md
```

**Custom output name:**
```powershell
python generate_pdf.py README_PARAMETER_REFERENCE.md MyCustomName.pdf
```

**All available files:**
```powershell
python generate_pdf.py README_PARAMETER_REFERENCE.md
python generate_pdf.py README_COMPLETE_PIPELINE.md
python generate_pdf.py README_PARAMETER_FLOW.md
python generate_pdf.py README_VERIFICATION_SYSTEM.md
python generate_pdf.py README_DOCUMENTATION_INDEX.md
```

---

### Generate All PDFs at Once

**Windows:**
```
generate_all_pdfs.bat
```

**Manual (PowerShell):**
```powershell
python generate_pdf.py README_PARAMETER_REFERENCE.md
python generate_pdf.py README_COMPLETE_PIPELINE.md
python generate_pdf.py README_PARAMETER_FLOW.md
python generate_pdf.py README_VERIFICATION_SYSTEM.md
python generate_pdf.py README_DOCUMENTATION_INDEX.md
```

---

## 📁 Output Files

After running the script, you'll find PDF files in the same directory:

```
WaveMaker/
├── README_PARAMETER_REFERENCE.md      → README_PARAMETER_REFERENCE.pdf
├── README_COMPLETE_PIPELINE.md        → README_COMPLETE_PIPELINE.pdf
├── README_PARAMETER_FLOW.md           → README_PARAMETER_FLOW.pdf
├── README_VERIFICATION_SYSTEM.md      → README_VERIFICATION_SYSTEM.pdf
└── README_DOCUMENTATION_INDEX.md      → README_DOCUMENTATION_INDEX.pdf
```

---

## 🛠️ Troubleshooting

### Error: "Pandoc not found"

**Solution 1:** Install Pandoc from https://pandoc.org/installing.html

**Solution 2:** Use Python libraries instead:
```powershell
pip install markdown weasyprint
python generate_pdf.py README_PARAMETER_REFERENCE.md
```

---

### Error: "No module named 'markdown'"

**Solution:**
```powershell
pip install markdown weasyprint
```

---

### Error: "weasyprint installation failed"

**For Windows**, WeasyPrint may need additional dependencies.

**Alternative solution:**
```powershell
pip install mdpdf
```

Then the script will try mdpdf automatically.

---

### Error: "Permission denied"

**Solution:** Close any PDF viewers that might have the file open, then try again.

---

## 🎨 PDF Features

The generated PDFs include:

✅ **Professional formatting**
- Styled headings and sections
- Syntax-highlighted code blocks
- Formatted tables
- Clear typography

✅ **Complete content**
- All text from markdown
- Code examples
- Tables and lists
- Links preserved (as text)

✅ **Easy navigation**
- Clear section breaks
- Consistent styling
- Readable fonts

---

## 🔧 Customization

To customize PDF styling, edit `generate_pdf.py`:

**Change margins:**
```python
# Line with geometry parameter
-V geometry:margin=1in  # Change to 0.5in, 2cm, etc.
```

**Change fonts (WeasyPrint method):**
```python
# In the CSS section
font-family: Arial, sans-serif;  # Change to your preferred font
font-size: 11pt;                  # Change size
```

---

## 📊 Method Comparison

| Method | Quality | Speed | Dependencies | Best For |
|--------|---------|-------|--------------|----------|
| Pandoc | ⭐⭐⭐⭐⭐ | Fast | External tool | Production |
| WeasyPrint | ⭐⭐⭐⭐ | Medium | Python libs | Development |
| mdpdf | ⭐⭐⭐ | Fast | Python libs | Quick exports |
| markdown2pdf | ⭐⭐⭐ | Fast | Python libs | Simple docs |

**Recommendation:** Use Pandoc for best results.

---

## 💡 Advanced Usage

### Generate PDF from Python Code

```python
from generate_pdf import generate_pdf_weasyprint

# Generate PDF
generate_pdf_weasyprint(
    "README_PARAMETER_REFERENCE.md",
    "output.pdf"
)
```

### Batch Processing

```python
import os
from generate_pdf import generate_pdf_pandoc

# Get all markdown files
md_files = [f for f in os.listdir('.') if f.endswith('.md') and f.startswith('README_')]

# Convert each to PDF
for md_file in md_files:
    pdf_file = md_file.replace('.md', '.pdf')
    generate_pdf_pandoc(md_file, pdf_file)
```

---

## 📖 Online Alternatives

If you prefer not to install anything, use online converters:

1. **Markdown to PDF** - https://www.markdowntopdf.com/
2. **Dillinger** - https://dillinger.io/ (export as PDF)
3. **Pandoc Online** - https://pandoc.org/try/

**Steps:**
1. Open the markdown file in a text editor
2. Copy all content
3. Paste into online converter
4. Download PDF

---

## ✅ Verification

To verify PDF generation worked:

1. **Check file exists:**
   ```powershell
   dir *.pdf
   ```

2. **Check file size:**
   - Should be 100KB-500KB for documentation files
   - If 0KB or very small, generation failed

3. **Open PDF:**
   - Double-click the PDF file
   - Verify content is readable and formatted correctly

---

## 🔄 Regenerating PDFs

If you update the markdown files, simply run the script again:

```powershell
python generate_pdf.py README_PARAMETER_REFERENCE.md
```

The old PDF will be overwritten with the updated version.

---

## 📞 Support

**Script not working?**
1. Check Python is installed: `python --version`
2. Try installing dependencies: `pip install -r pdf_requirements.txt`
3. Try Pandoc instead: https://pandoc.org/installing.html

**Generated PDF looks wrong?**
1. Try different method (Pandoc vs WeasyPrint)
2. Check markdown file has proper formatting
3. Open an issue with the error message

---

## 📝 Summary

**To generate PDF from README_PARAMETER_REFERENCE.md:**

1. **Install Pandoc** (recommended): https://pandoc.org/installing.html
   
   OR
   
   **Install Python libs**: `pip install markdown weasyprint`

2. **Run script**: `python generate_pdf.py README_PARAMETER_REFERENCE.md`

3. **Open PDF**: `README_PARAMETER_REFERENCE.pdf`

**Done!** 🎉
