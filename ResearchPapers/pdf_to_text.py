import os
import fitz  # PyMuPDF

INPUT_FOLDER = os.getcwd()
OUTPUT_FOLDER = os.path.join(INPUT_FOLDER, "Extracted_TextFiles")

def extract_text_from_pdf(pdf_path):
    text = []
    try:
        doc = fitz.open(pdf_path)
        for page in doc:
            text.append(page.get_text("text"))
        doc.close()
    except Exception as e:
        print(f"[ERROR] Failed to read {pdf_path}: {e}")
    return "\n".join(text)

def main():
    if not os.path.exists(OUTPUT_FOLDER):
        os.makedirs(OUTPUT_FOLDER)

    pdf_files = [f for f in os.listdir(INPUT_FOLDER) if f.lower().endswith(".pdf")]

    if not pdf_files:
        print("No PDF files found in this folder.")
        return

    for pdf_file in pdf_files:
        pdf_path = os.path.join(INPUT_FOLDER, pdf_file)
        output_file = os.path.splitext(pdf_file)[0] + ".txt"
        output_path = os.path.join(OUTPUT_FOLDER, output_file)

        print(f"Processing: {pdf_file}")

        text = extract_text_from_pdf(pdf_path)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text)

    print("\nDone. Text files saved in 'Extracted_TextFiles' folder.")

if __name__ == "__main__":
    main()