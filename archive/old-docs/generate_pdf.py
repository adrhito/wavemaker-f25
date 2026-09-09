"""
PDF Generator for WaveMaker Documentation
Converts markdown README files to PDF format
"""

import os
import sys

def generate_pdf_pandoc(markdown_file, output_pdf=None):
    """
    Generate PDF using pandoc (recommended method)
    Requires pandoc to be installed: https://pandoc.org/installing.html
    """
    if output_pdf is None:
        output_pdf = markdown_file.replace('.md', '.pdf')
    
    command = f'pandoc "{markdown_file}" -o "{output_pdf}" --pdf-engine=xelatex -V geometry:margin=1in'
    
    print(f"Generating PDF using pandoc...")
    print(f"Input: {markdown_file}")
    print(f"Output: {output_pdf}")
    
    result = os.system(command)
    
    if result == 0:
        print(f"✓ PDF generated successfully: {output_pdf}")
        return True
    else:
        print("✗ Pandoc not found or failed. Try alternative method below.")
        return False


def generate_pdf_markdown2pdf(markdown_file, output_pdf=None):
    """
    Generate PDF using markdown2pdf library
    Install: pip install markdown2pdf
    """
    try:
        from markdown2pdf import convert
        
        if output_pdf is None:
            output_pdf = markdown_file.replace('.md', '.pdf')
        
        print(f"Generating PDF using markdown2pdf...")
        print(f"Input: {markdown_file}")
        print(f"Output: {output_pdf}")
        
        convert(markdown_file, output_pdf)
        print(f"✓ PDF generated successfully: {output_pdf}")
        return True
    except ImportError:
        print("✗ markdown2pdf not installed. Run: pip install markdown2pdf")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def generate_pdf_mdpdf(markdown_file, output_pdf=None):
    """
    Generate PDF using mdpdf library
    Install: pip install mdpdf
    """
    try:
        from mdpdf.core import MDPDF
        
        if output_pdf is None:
            output_pdf = markdown_file.replace('.md', '.pdf')
        
        print(f"Generating PDF using mdpdf...")
        print(f"Input: {markdown_file}")
        print(f"Output: {output_pdf}")
        
        pdf = MDPDF(filename=output_pdf)
        pdf.add_markdown(markdown_file)
        pdf.save()
        
        print(f"✓ PDF generated successfully: {output_pdf}")
        return True
    except ImportError:
        print("✗ mdpdf not installed. Run: pip install mdpdf")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def generate_pdf_weasyprint(markdown_file, output_pdf=None):
    """
    Generate PDF using markdown + weasyprint
    Install: pip install markdown weasyprint
    """
    try:
        import markdown
        from weasyprint import HTML, CSS
        
        if output_pdf is None:
            output_pdf = markdown_file.replace('.md', '.pdf')
        
        print(f"Generating PDF using weasyprint...")
        print(f"Input: {markdown_file}")
        print(f"Output: {output_pdf}")
        
        # Read markdown file
        with open(markdown_file, 'r', encoding='utf-8') as f:
            md_content = f.read()
        
        # Convert markdown to HTML
        html_content = markdown.markdown(
            md_content, 
            extensions=['tables', 'fenced_code', 'codehilite']
        )
        
        # Add CSS styling
        styled_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    font-size: 11pt;
                    line-height: 1.6;
                    margin: 40px;
                }}
                h1 {{
                    color: #2c3e50;
                    border-bottom: 3px solid #3498db;
                    padding-bottom: 10px;
                }}
                h2 {{
                    color: #34495e;
                    border-bottom: 2px solid #95a5a6;
                    padding-bottom: 8px;
                    margin-top: 30px;
                }}
                h3 {{
                    color: #7f8c8d;
                    margin-top: 20px;
                }}
                code {{
                    background-color: #f4f4f4;
                    padding: 2px 6px;
                    border-radius: 3px;
                    font-family: 'Courier New', monospace;
                    font-size: 10pt;
                }}
                pre {{
                    background-color: #f8f8f8;
                    border: 1px solid #ddd;
                    border-radius: 5px;
                    padding: 15px;
                    overflow-x: auto;
                }}
                table {{
                    border-collapse: collapse;
                    width: 100%;
                    margin: 20px 0;
                }}
                th, td {{
                    border: 1px solid #ddd;
                    padding: 8px;
                    text-align: left;
                }}
                th {{
                    background-color: #3498db;
                    color: white;
                }}
                tr:nth-child(even) {{
                    background-color: #f2f2f2;
                }}
                strong {{
                    color: #2c3e50;
                }}
            </style>
        </head>
        <body>
            {html_content}
        </body>
        </html>
        """
        
        # Convert HTML to PDF
        HTML(string=styled_html).write_pdf(output_pdf)
        
        print(f"✓ PDF generated successfully: {output_pdf}")
        return True
    except ImportError as e:
        print(f"✗ Required library not installed: {e}")
        print("Run: pip install markdown weasyprint")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def main():
    """Main function to generate PDF from markdown"""
    
    # Default file
    default_file = r"c:\Users\admin\Documents\softwarelab\WaveMaker\README_PARAMETER_REFERENCE.md"
    
    # Get file from command line or use default
    if len(sys.argv) > 1:
        markdown_file = sys.argv[1]
    else:
        markdown_file = default_file
    
    # Check if file exists
    if not os.path.exists(markdown_file):
        print(f"Error: File not found: {markdown_file}")
        return
    
    # Get output filename
    if len(sys.argv) > 2:
        output_pdf = sys.argv[2]
    else:
        output_pdf = markdown_file.replace('.md', '.pdf')
    
    print("=" * 70)
    print("WaveMaker Documentation PDF Generator")
    print("=" * 70)
    print()
    
    # Try methods in order of preference
    methods = [
        ("Pandoc (Best Quality)", generate_pdf_pandoc),
        ("WeasyPrint (Good Quality)", generate_pdf_weasyprint),
        ("mdpdf", generate_pdf_mdpdf),
        ("markdown2pdf", generate_pdf_markdown2pdf),
    ]
    
    success = False
    for method_name, method_func in methods:
        print(f"\nTrying method: {method_name}")
        print("-" * 70)
        try:
            if method_func(markdown_file, output_pdf):
                success = True
                break
        except Exception as e:
            print(f"✗ Failed: {e}")
            continue
    
    print()
    print("=" * 70)
    if success:
        print("✓ PDF GENERATION COMPLETE")
        print(f"✓ File saved to: {output_pdf}")
        print()
        print("You can open the PDF with any PDF viewer.")
    else:
        print("✗ PDF GENERATION FAILED")
        print()
        print("INSTALLATION OPTIONS:")
        print()
        print("Option 1 (Recommended): Install Pandoc")
        print("  Download from: https://pandoc.org/installing.html")
        print("  - Easiest method, best quality output")
        print()
        print("Option 2: Install Python libraries")
        print("  Run: pip install markdown weasyprint")
        print("  Then run this script again")
        print()
        print("Option 3: Use online converter")
        print("  Upload markdown to: https://www.markdowntopdf.com/")
    print("=" * 70)


if __name__ == "__main__":
    main()
