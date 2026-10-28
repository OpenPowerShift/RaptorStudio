# opsmodel — Asciidoctor inline macro for the RAPTOR Studio help docs.
#
#   opsmodel:Lantern1[]              -> resolved via models/Lantern1.rbds, then .rbd
#   opsmodel:Lantern1.rbds[]
#   opsmodel:models/Lantern1.rbd[]   -> any doc-relative path with extension
#
# Renders an in-app "open model" button: <a href="opsmodel://models/…">.
# The RAPTOR Studio help viewer intercepts that scheme and opens the
# model; any other environment (plain browser) ignores it harmlessly.
#
# Usage with the Ruby asciidoctor CLI:
#   asciidoctor -r new_docs/tools/ops_adoc_ext.rb new_docs/book.adoc

require 'asciidoctor'
require 'asciidoctor/extensions'

class OpsmodelMacro < Asciidoctor::Extensions::InlineMacroProcessor
  use_dsl
  named :opsmodel
  name_positional_attributes 'caption'

  MODEL_EXTS = %w(.rbds .rbd).freeze

  def process(parent, target, attrs)
    doc = parent.document
    rel = target.include?('/') ? target : "models/#{target}"
    resolved = resolve_model(doc, rel)
    if resolved
      href = "opsmodel://#{resolved}"
      name = File.basename(resolved, File.extname(resolved))
    else
      doc.logger.warn %(opsmodel: no model file found for '#{target}' \
(looked for #{MODEL_EXTS.map { |e| rel + e }.join(' and ')}) \
relative to #{doc.base_dir})
      href = "opsmodel://#{rel}"
      name = File.basename(rel, File.extname(rel))
    end
    caption = attrs['caption'] || "Open #{name} in RAPTOR Studio"

    %(<a href="#{href}" class="ops-open-model" \
title="#{caption.gsub('"', '&quot;')}">#{caption}</a>)
  end

  private

  # Try the target as given, then with each known extension appended.
  # Return a path relative to the document root, or nil.
  def resolve_model(doc, rel)
    candidates = rel.end_with?('.rbds', '.rbd') ? [rel] : MODEL_EXTS.map { |e| rel + e }
    candidates.each do |cand|
      abs = doc.normalize_system_path(cand)
      return cand if File.file?(abs)
    end
    nil
  end
end

Asciidoctor::Extensions.register do
  inline_macro OpsmodelMacro
end
