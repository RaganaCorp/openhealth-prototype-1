// Seed catalog for the intake "Health conditions" step. Plain-language label
// with the clinical term in parentheses, kept ICD-recognizable. Codes are stable
// slugs used to dedupe selections; they are not ICD codes.

export type ConditionSeed = {
  code: string;
  label: string;
};

export type ConditionCategory = {
  name: string;
  conditions: ConditionSeed[];
};

export const CONDITION_CATEGORIES: ConditionCategory[] = [
  {
    name: "Heart & Circulation",
    conditions: [
      { code: "hypertension", label: "High blood pressure (hypertension)" },
      { code: "hyperlipidemia", label: "High cholesterol (hyperlipidemia)" },
      { code: "cad", label: "Coronary artery disease (CAD)" },
      { code: "afib", label: "Atrial fibrillation (AFib)" },
      { code: "chf", label: "Heart failure (CHF)" },
      { code: "mi", label: "Previous heart attack (MI)" },
      { code: "dvt", label: "Deep vein thrombosis (DVT)" },
      { code: "pad", label: "Peripheral artery disease (PAD)" },
      { code: "valve_disease", label: "Heart valve disease" },
      { code: "arrhythmia", label: "Arrhythmia (irregular heartbeat)" },
    ],
  },
  {
    name: "Hormones & Metabolism",
    conditions: [
      { code: "type2_diabetes", label: "Type 2 diabetes (diabetes mellitus type 2)" },
      { code: "type1_diabetes", label: "Type 1 diabetes (diabetes mellitus type 1)" },
      { code: "prediabetes", label: "Prediabetes (impaired glucose tolerance)" },
      { code: "hypothyroidism", label: "Underactive thyroid (hypothyroidism)" },
      { code: "hyperthyroidism", label: "Overactive thyroid (hyperthyroidism)" },
      { code: "obesity", label: "Obesity" },
      { code: "pcos", label: "Polycystic ovary syndrome (PCOS)" },
      { code: "metabolic_syndrome", label: "Metabolic syndrome" },
      { code: "gout", label: "Gout (hyperuricemia)" },
      { code: "osteoporosis_metabolic", label: "Vitamin D deficiency" },
    ],
  },
  {
    name: "Brain & Nerves",
    conditions: [
      { code: "migraine", label: "Migraine headaches (migraine)" },
      { code: "stroke", label: "Stroke (cerebrovascular accident)" },
      { code: "tia", label: "Mini-stroke (transient ischemic attack)" },
      { code: "epilepsy", label: "Seizure disorder (epilepsy)" },
      { code: "parkinsons", label: "Parkinson's disease" },
      { code: "alzheimers", label: "Alzheimer's disease (dementia)" },
      { code: "ms", label: "Multiple sclerosis (MS)" },
      { code: "neuropathy", label: "Nerve pain / numbness (peripheral neuropathy)" },
      { code: "concussion", label: "Concussion (traumatic brain injury)" },
      { code: "restless_legs", label: "Restless legs syndrome" },
    ],
  },
  {
    name: "Lungs & Breathing",
    conditions: [
      { code: "asthma", label: "Asthma" },
      { code: "copd", label: "Chronic obstructive pulmonary disease (COPD)" },
      { code: "sleep_apnea", label: "Sleep apnea (obstructive sleep apnea)" },
      { code: "pneumonia", label: "Pneumonia (recurrent)" },
      { code: "pulmonary_fibrosis", label: "Lung scarring (pulmonary fibrosis)" },
      { code: "bronchitis", label: "Chronic bronchitis" },
      { code: "pulmonary_embolism", label: "Blood clot in lung (pulmonary embolism)" },
      { code: "allergic_rhinitis", label: "Seasonal allergies (allergic rhinitis)" },
      { code: "tuberculosis", label: "Tuberculosis (TB)" },
    ],
  },
  {
    name: "Digestive System",
    conditions: [
      { code: "gerd", label: "Acid reflux (GERD)" },
      { code: "ibs", label: "Irritable bowel syndrome (IBS)" },
      { code: "crohns", label: "Crohn's disease" },
      { code: "ulcerative_colitis", label: "Ulcerative colitis" },
      { code: "celiac", label: "Celiac disease" },
      { code: "gallstones", label: "Gallstones (cholelithiasis)" },
      { code: "peptic_ulcer", label: "Stomach ulcer (peptic ulcer disease)" },
      { code: "fatty_liver", label: "Fatty liver disease (NAFLD)" },
      { code: "diverticulitis", label: "Diverticulitis" },
      { code: "hepatitis", label: "Hepatitis" },
    ],
  },
  {
    name: "Kidneys & Urinary",
    conditions: [
      { code: "ckd", label: "Chronic kidney disease (CKD)" },
      { code: "kidney_stones", label: "Kidney stones (nephrolithiasis)" },
      { code: "uti", label: "Recurrent urinary tract infections (UTI)" },
      { code: "bph", label: "Enlarged prostate (BPH)" },
      { code: "incontinence", label: "Urinary incontinence" },
      { code: "overactive_bladder", label: "Overactive bladder" },
      { code: "glomerulonephritis", label: "Kidney inflammation (glomerulonephritis)" },
      { code: "polycystic_kidney", label: "Polycystic kidney disease" },
      { code: "dialysis", label: "Kidney failure on dialysis (ESRD)" },
    ],
  },
  {
    name: "Bones & Joints",
    conditions: [
      { code: "osteoarthritis", label: "Osteoarthritis" },
      { code: "rheumatoid_arthritis", label: "Rheumatoid arthritis" },
      { code: "osteoporosis", label: "Thinning bones (osteoporosis)" },
      { code: "low_back_pain", label: "Chronic low back pain" },
      { code: "gout_joint", label: "Gout" },
      { code: "fibromyalgia", label: "Fibromyalgia" },
      { code: "herniated_disc", label: "Herniated disc" },
      { code: "scoliosis", label: "Scoliosis" },
      { code: "tendinitis", label: "Tendinitis" },
      { code: "fracture_history", label: "History of bone fracture" },
    ],
  },
  {
    name: "Skin",
    conditions: [
      { code: "eczema", label: "Eczema (atopic dermatitis)" },
      { code: "psoriasis", label: "Psoriasis" },
      { code: "acne", label: "Acne" },
      { code: "rosacea", label: "Rosacea" },
      { code: "skin_cancer", label: "Skin cancer (melanoma / carcinoma)" },
      { code: "hives", label: "Hives (urticaria)" },
      { code: "shingles", label: "Shingles (herpes zoster)" },
      { code: "contact_dermatitis", label: "Contact dermatitis" },
      { code: "vitiligo", label: "Vitiligo" },
    ],
  },
  {
    name: "Mental Health",
    conditions: [
      { code: "depression", label: "Depression (major depressive disorder)" },
      { code: "anxiety", label: "Anxiety (generalized anxiety disorder)" },
      { code: "bipolar", label: "Bipolar disorder" },
      { code: "ptsd", label: "Post-traumatic stress disorder (PTSD)" },
      { code: "ocd", label: "Obsessive-compulsive disorder (OCD)" },
      { code: "adhd", label: "Attention-deficit/hyperactivity disorder (ADHD)" },
      { code: "panic_disorder", label: "Panic disorder" },
      { code: "substance_use", label: "Substance use disorder" },
      { code: "eating_disorder", label: "Eating disorder" },
      { code: "insomnia", label: "Insomnia" },
    ],
  },
  {
    name: "Blood & Immune",
    conditions: [
      { code: "anemia", label: "Anemia (iron-deficiency anemia)" },
      { code: "lupus", label: "Lupus (systemic lupus erythematosus)" },
      { code: "hiv", label: "HIV / AIDS" },
      { code: "lymphoma", label: "Lymphoma" },
      { code: "leukemia", label: "Leukemia" },
      { code: "clotting_disorder", label: "Clotting disorder (thrombophilia)" },
      { code: "sickle_cell", label: "Sickle cell disease" },
      { code: "thyroiditis_autoimmune", label: "Autoimmune thyroiditis (Hashimoto's)" },
      { code: "immunodeficiency", label: "Immune deficiency" },
    ],
  },
  {
    name: "Eyes & Vision",
    conditions: [
      { code: "myopia", label: "Nearsightedness (myopia)" },
      { code: "cataracts", label: "Cataracts" },
      { code: "glaucoma", label: "Glaucoma" },
      { code: "macular_degeneration", label: "Age-related macular degeneration" },
      { code: "diabetic_retinopathy", label: "Diabetic retinopathy" },
      { code: "dry_eye", label: "Dry eye syndrome" },
      { code: "astigmatism", label: "Astigmatism" },
      { code: "color_blindness", label: "Color blindness" },
      { code: "conjunctivitis", label: "Recurrent pink eye (conjunctivitis)" },
    ],
  },
  {
    name: "Reproductive Health",
    conditions: [
      { code: "endometriosis", label: "Endometriosis" },
      { code: "uterine_fibroids", label: "Uterine fibroids" },
      { code: "pcos_repro", label: "Polycystic ovary syndrome (PCOS)" },
      { code: "infertility", label: "Infertility" },
      { code: "menopause", label: "Menopause symptoms" },
      { code: "erectile_dysfunction", label: "Erectile dysfunction" },
      { code: "prostate_cancer", label: "Prostate cancer" },
      { code: "stis", label: "Sexually transmitted infection (STI)" },
      { code: "ovarian_cysts", label: "Ovarian cysts" },
    ],
  },
];
