package com.example.speako.global.config;
import io.swagger.v3.oas.models.OpenAPI;
import io.swagger.v3.oas.models.info.Info;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class SwaggerConfig {
    @Bean
    public OpenAPI openAPI() {
        return new OpenAPI()
                .info(new Info()
                        .title("동아리 프로젝트 API 명세서")
                        .description("대본 생성 및 발표 코칭 서비스 API")
                        .version("v1.0.0"));
    }
}
