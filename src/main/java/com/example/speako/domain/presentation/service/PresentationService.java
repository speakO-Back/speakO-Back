package com.example.speako.domain.presentation.service;

import com.example.speako.domain.presentation.dto.PresentationRequestDTO;
import com.example.speako.domain.presentation.dto.PresentationResponseDTO;
import com.example.speako.domain.presentation.entity.Presentation;
import com.example.speako.domain.presentation.entity.Slide;
import com.example.speako.domain.presentation.repository.PresentationRepository;

import com.example.speako.domain.script.entity.Script;
import com.example.speako.domain.script.repository.ScriptRepository;
import com.example.speako.domain.slide.repository.SlideRepository;
import com.example.speako.domain.user.entity.User;
import com.example.speako.domain.user.repository.UserRepository;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.HttpStatusCodeException;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

import java.io.IOException;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@Service
@RequiredArgsConstructor
public class PresentationService {
    private final UserRepository userRepository;
    private final PresentationRepository presentationRepository;
    private final ScriptRepository scriptRepository;
    private final SlideRepository slideRepository;
    private final S3Service s3Service;
    private final ObjectMapper objectMapper = new ObjectMapper();

    private static final String AI_BASE_URL =
            System.getenv().getOrDefault("AI_BASE_URL",
                    "https://losing-charity-floyd-transparent.trycloudflare.com");

    private static void addApiKeyHeader(HttpHeaders headers) {
        String apiKey = System.getenv("SPEAKO_API_KEY");
        if (apiKey != null && !apiKey.isBlank()) {
            headers.set("X-API-Key", apiKey);
        }
    }

    @Transactional
    public Long generatePresentationAndScript(MultipartFile file, PresentationRequestDTO.CreateDTO request, String email) {
        User user = userRepository.findByEmail(email)
                .orElseThrow(() -> new IllegalArgumentException("존재하지 않는 회원입니다."));

        if (file.isEmpty() || file.getOriginalFilename() == null) {
            throw new IllegalArgumentException("업로드된 파일이 비어있습니다.");
        }

        String originalFilename = file.getOriginalFilename();
        String extension = originalFilename.substring(originalFilename.lastIndexOf(".") + 1).toLowerCase();

        if (!extension.equals("pptx") && !extension.equals("pdf")) {
            throw new IllegalArgumentException("PPTX 또는 PDF 파일만 업로드 가능합니다.");
        }

        long maxFileSize = 20 * 1024 * 1024; // 20MB
        if (file.getSize() > maxFileSize) {
            throw new IllegalArgumentException("파일 용량은 최대 20MB를 초과할 수 없습니다.");
        }

        float fileSizeMB = (float) file.getSize() / (1024 * 1024);

        final byte[] fileBytes;
        try {
            fileBytes = file.getBytes();
        } catch (IOException e) {
            throw new RuntimeException("파일 읽기 실패", e);
        }

        String fileUrl = s3Service.uploadFile(file);

        long aiProjectId;
        int slideCount;
        {
            Map aiResponse = createAiProject(fileBytes, originalFilename, request.getTopic());
            aiProjectId = ((Number) aiResponse.get("project_id")).longValue();
            slideCount = extractSlideCount(aiResponse);
        }

        Presentation presentation = Presentation.builder()
                .user(user)
                .fileName(originalFilename)
                .fileType(extension.equals("pdf") ? Presentation.FileType.pdf : Presentation.FileType.ppt)
                .fileSize(fileSizeMB)
                .slideCount(slideCount)
                .aiProjectId(aiProjectId)
                .topic(request.getTopic())
                .duration(request.getDuration())
                .tone(Presentation.Tone.valueOf(request.getTone()))
                .guideline(request.getGuideline())
                .fileUrl(fileUrl)
                .build();

        presentationRepository.save(presentation);

        callPythonAiServerForScript(presentation, request.getTopic(), request.getDuration(), request.getTone(), request.getGuideline());

        return presentation.getPresentationId();
    }

    private Map createAiProject(byte[] fileBytes, String filename, String topic) {
        RestTemplate restTemplate = new RestTemplate();
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(10_000);
        factory.setReadTimeout(300_000);
        restTemplate.setRequestFactory(factory);

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.MULTIPART_FORM_DATA);
        addApiKeyHeader(headers);

        MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
        body.add("file", new ByteArrayResource(fileBytes) {
            @Override
            public String getFilename() {
                return filename;
            }
        });
        if (topic != null && !topic.isBlank()) {
            body.add("topic", topic);
        }

        try {
            ResponseEntity<Map> response = restTemplate.postForEntity(
                    AI_BASE_URL + "/api/projects",
                    new HttpEntity<>(body, headers),
                    Map.class
            );
            Map responseBody = response.getBody();
            if (responseBody == null || !(responseBody.get("project_id") instanceof Number)) {
                throw new IllegalStateException("AI 서버 업로드 응답에 project_id가 없습니다: " + responseBody);
            }
            return responseBody;
        } catch (HttpStatusCodeException e) {
            throw new ResponseStatusException(e.getStatusCode(), extractDetail(e.getResponseBodyAsString()));
        }
    }

    @SuppressWarnings("unchecked")
    private int extractSlideCount(Map aiResponse) {
        Object data = aiResponse.get("data");
        if (data instanceof Map) {
            Object slides = ((Map<String, Object>) data).get("slides");
            if (slides instanceof List) {
                return ((List<?>) slides).size();
            }
        }
        return 0;
    }

    private void callPythonAiServerForScript(Presentation presentation, String topic, Integer duration, String tone, String guideline) {
        RestTemplate restTemplate = new RestTemplate();
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(10_000);
        factory.setReadTimeout(30_000);
        restTemplate.setRequestFactory(factory);

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        addApiKeyHeader(headers);

        Map<String, Object> body = new HashMap<>();
        body.put("project_id", presentation.getAiProjectId());
        body.put("topic", topic);
        body.put("presentation_time", duration);
        body.put("style", tone);
        body.put("extra_requirement", guideline);

        HttpEntity<Map<String, Object>> requestEntity = new HttpEntity<>(body, headers);

        String jobId;
        try {
            ResponseEntity<Map> response = restTemplate.postForEntity(
                    AI_BASE_URL + "/api/script/full",
                    requestEntity,
                    Map.class
            );

            Map responseBody = response.getBody();
            if (responseBody == null || !responseBody.containsKey("job_id")) {
                throw new IllegalStateException("AI 서버로부터 Job ID를 받지 못했습니다.");
            }
            jobId = responseBody.get("job_id").toString();

        } catch (HttpStatusCodeException e) {
            String errorBody = e.getResponseBodyAsString();
            System.err.println("=== AI 서버 에러 상세 내용 ===");
            System.err.println(errorBody);
            throw new ResponseStatusException(e.getStatusCode(), "AI 대본 생성 요청 실패: " + errorBody);
        }

        HttpEntity<Void> pollEntity = new HttpEntity<>(headers);
        int maxRetry = 120;
        boolean isCompleted = false;

        for (int i = 0; i < maxRetry; i++) {
            try {
                Thread.sleep(3000);

                ResponseEntity<Map> statusResponse = restTemplate.exchange(
                        AI_BASE_URL + "/api/script/jobs/" + jobId,
                        HttpMethod.GET,
                        pollEntity,
                        Map.class
                );

                Map statusBody = statusResponse.getBody();
                if (statusBody != null) {
                    String status = (String) statusBody.get("status");

                    if ("completed".equalsIgnoreCase(status)) {
                        saveScripts(presentation, statusBody);
                        isCompleted = true;
                        break;
                    } else if ("failed".equalsIgnoreCase(status)) {
                        throw new RuntimeException("AI 대본 생성 작업이 실패했습니다: " + statusBody.get("error"));
                    }
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new RuntimeException("폴링 대기 중 인터럽트가 발생했습니다.", e);
            } catch (HttpStatusCodeException e) {
                System.err.println("폴링 중 오류(계속 시도): " + e.getStatusCode());
            }
        }

        if (!isCompleted) {
            throw new IllegalStateException("대본 생성이 제한 시간 안에 끝나지 않았습니다 (job_id=" + jobId + ")");
        }
    }

    @SuppressWarnings("unchecked")
    private void saveScripts(Presentation presentation, Map statusBody) {
        Map<String, Object> data = (Map<String, Object>) statusBody.get("data");
        if (data == null) {
            throw new IllegalStateException("완료 응답에 data가 없습니다: " + statusBody);
        }
        List<Map<String, Object>> slides = (List<Map<String, Object>>) data.get("slides");
        if (slides == null || slides.isEmpty()) {
            throw new IllegalStateException("완료 응답에 slides가 없습니다: " + data);
        }

        for (Map<String, Object> item : slides) {
            int slideNumber = Integer.parseInt(String.valueOf(item.get("slide_number")));
            String scriptText = String.valueOf(item.get("script"));

            Slide slide = Slide.builder()
                    .presentation(presentation)
                    .slideOrder(slideNumber)
                    .build();
            slideRepository.save(slide);

            Script script = Script.builder()
                    .slide(slide)
                    .content(scriptText)
                    .build();
            scriptRepository.save(script);
        }
    }

    private String extractDetail(String responseBody) {
        try {
            JsonNode node = objectMapper.readTree(responseBody);
            if (node.has("detail")) {
                return node.get("detail").asText();
            }
        } catch (Exception ignored) {
        }
        return responseBody;
    }

    @Transactional(readOnly = true)
    public PresentationResponseDTO.DetailDTO getPresentationDetails(Long presentationId) {
        Presentation presentation = presentationRepository.findById(presentationId)
                .orElseThrow(() -> new IllegalArgumentException("존재하지 않는 발표 자료입니다."));

        List<PresentationResponseDTO.SlideScriptDTO> slideScripts = presentation.getSlides().stream()
                .map(slide -> {
                    Script latestScript = slide.getScripts().stream()
                            .max(Comparator.comparing(Script::getVersion))
                            .orElse(null);

                    return PresentationResponseDTO.SlideScriptDTO.builder()
                            .slideId(slide.getSlideId())
                            .slideOrder(slide.getSlideOrder())
                            .slideTitle(slide.getSlideTitle())
                            .rawText(slide.getRawText())
                            .scriptId(latestScript != null ? latestScript.getScriptId() : null)
                            .content(latestScript != null ? latestScript.getContent() : "생성된 대본이 없습니다.")
                            .version(latestScript != null ? latestScript.getVersion() : 1)
                            .build();
                })
                .sorted(Comparator.comparing(PresentationResponseDTO.SlideScriptDTO::getSlideOrder))
                .collect(Collectors.toList());

        return PresentationResponseDTO.DetailDTO.builder()
                .presentationId(presentation.getPresentationId())
                .topic(presentation.getTopic())
                .duration(presentation.getDuration())
                .tone(presentation.getTone().name())
                .fileUrl(presentation.getFileUrl())
                .slides(slideScripts)
                .build();
    }

    /** 🔄 전체 대본 재생성 (유저 수정 대본 및 설정값 반영) */
    @Transactional
    public PresentationResponseDTO.DetailDTO regenerateAll(Long presentationId, Integer duration, String tone, String extraRequirement, String currentScript) {
        Presentation presentation = presentationRepository.findById(presentationId)
                .orElseThrow(() -> new IllegalArgumentException("발표를 찾을 수 없습니다."));
        if (presentation.getAiProjectId() == null) {
            throw new IllegalStateException("이 발표에는 AI 프로젝트 번호가 없습니다. 자료를 다시 업로드해주세요.");
        }

        // 기존 슬라이드·대본 삭제
        List<Slide> old = slideRepository.findByPresentation(presentation);
        for (Slide s : old) {
            scriptRepository.deleteBySlide(s);
        }
        slideRepository.deleteAll(old);

        if (duration != null) presentation.updateDuration(duration);
        if (tone != null && !tone.isBlank()) presentation.updateTone(Presentation.Tone.valueOf(tone));
        if (extraRequirement != null) presentation.updateGuideline(extraRequirement);

        // TODO: 만약 AI 서버 전체 재생성 API 규격에 currentScript를 전달해야 한다면 아래 통신 로직에 추가 가능
        callPythonAiServerForScript(presentation, presentation.getTopic(), presentation.getDuration(), presentation.getTone().name(), presentation.getGuideline());

        return getPresentationDetails(presentationId);
    }

    /** 🔄 슬라이드 하나만 부분 재생성 (유저가 수정한 기존 대본 반영) */
    @Transactional
    public PresentationResponseDTO.DetailDTO regenerateOne(Long presentationId, Long scriptId, String tone, String extraRequirement, String currentScript) {
        Script script = scriptRepository.findWithSlideAndPresentation(scriptId)
                .orElseThrow(() -> new IllegalArgumentException("대본을 찾을 수 없습니다."));

        Presentation presentation = script.getSlide().getPresentation();
        if (!presentation.getPresentationId().equals(presentationId)) {
            throw new IllegalArgumentException("이 발표의 대본이 아닙니다.");
        }
        if (presentation.getAiProjectId() == null) {
            throw new IllegalStateException("이 발표에는 AI 프로젝트 번호가 없습니다. 자료를 다시 업로드해주세요.");
        }

        RestTemplate restTemplate = new RestTemplate();
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(10_000);
        factory.setReadTimeout(120_000);
        restTemplate.setRequestFactory(factory);

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        addApiKeyHeader(headers);

        Map<String, Object> body = new HashMap<>();
        body.put("project_id", presentation.getAiProjectId());
        body.put("target_slide", script.getSlide().getSlideOrder());
        body.put("style", (tone != null && !tone.isBlank()) ? tone : presentation.getTone().name());
        body.put("extra_requirement", (extraRequirement != null && !extraRequirement.isBlank()) ? extraRequirement : presentation.getGuideline());

        // 👈 프론트엔드가 보내준 수정한 대본이 있다면 AI 서버로 함께 전달
        if (currentScript != null && !currentScript.isBlank()) {
            body.put("current_script", currentScript);
        } else {
            // 전달받은 게 없다면 DB에 있는 기존 최신 대본 내용 전송
            body.put("current_script", script.getContent());
        }

        Map response;
        try {
            response = restTemplate.postForEntity(
                    AI_BASE_URL + "/api/script/partial", new HttpEntity<>(body, headers), Map.class).getBody();
        } catch (HttpStatusCodeException e) {
            throw new ResponseStatusException(e.getStatusCode(), extractDetail(e.getResponseBodyAsString()));
        }

        Map<String, Object> data = response == null ? null : (Map<String, Object>) response.get("data");
        if (data == null || data.get("script") == null) {
            throw new IllegalStateException("AI 서버가 재생성 대본을 주지 않았습니다: " + response);
        }

        script.updateContent(String.valueOf(data.get("script")));
        scriptRepository.save(script);

        return getPresentationDetails(presentationId);
    }
}